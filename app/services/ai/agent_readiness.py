"""Readiness rules shared by publishing and sub-agent delegation."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.services.ai.agent_types import (
    AgentType,
    LOCKED_CAPABILITY_BY_TYPE,
    normalize_agent_capabilities,
)


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
        elif hasattr(entry, "name"):
            if getattr(entry, "enabled", True) is False:
                continue
            name = str(getattr(entry, "name", "") or "").strip()
        else:
            name = str(entry or "").strip()
        if name:
            names.add(name)
    return names


def has_knowledge_binding(
    *,
    capabilities: Iterable[str] | None,
    engine_config: Mapping[str, Any] | None,
    tools: Iterable[Any] | None,
    agent_type: AgentType | str | None = None,
) -> bool:
    """Return whether an agent is actually authorized and equipped for KB retrieval.

    A dataset visible through user permissions is not enough to grant a general
    agent knowledge-search authority. The capability, explicitly configured
    search tool, and at least one bound dataset must all be present.
    """
    # Older rows may predate the locked primary capability backfill. Keep the
    # compatibility in memory so routing works before the migration is applied.
    capability_set = {str(value).strip() for value in capabilities or []}
    if agent_type is not None:
        # Do not remove an explicitly stored legacy capability here. Some
        # callers do not persist agent_type alongside the capability list.
        capability_set.add(
            LOCKED_CAPABILITY_BY_TYPE[AgentType(agent_type)]
        )
    config = engine_config or {}
    dataset_ids = [value for value in config.get("dataset_ids", []) or [] if value]
    return (
        "knowledge_base" in capability_set
        and KNOWLEDGE_BASE_TOOL in _enabled_tool_names(tools)
        and bool(dataset_ids)
    )


def evaluate_agent_readiness(
    *,
    agent_type: AgentType | str,
    capabilities: Iterable[str] | None,
    engine_config: Mapping[str, Any] | None,
    tools: Iterable[Any] | None,
    has_published_version: bool,
    default_dataset_ids: Iterable[str] | None = None,
) -> AgentReadiness:
    """Evaluate whether an agent can actually run (publish / delegation gate).

    ``capabilities``/``engine_config`` live on the agent row (``ai_agents``) and
    are absent from ``ai_agent_versions``; only ``tools`` is version-scoped.
    Callers must not mix the two sources up.

    ``default_dataset_ids`` is the system-level fallback
    (``knowledge_ragflow_dataset_ids``).  A KNOWLEDGE_BASE agent without its own
    binding still resolves that fallback at query time, so it counts as bound
    here as well — otherwise the built-in 知识库助手 answers but is never ready.
    """
    normalized_type = AgentType(agent_type)
    # Creation/update paths already normalize capabilities, but legacy rows
    # can still be missing their type's locked capability. Do not mutate the
    # ORM object here; normalize only for the current readiness evaluation.
    capability_set = set(normalize_agent_capabilities(normalized_type, capabilities))
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
        effective_dataset_ids = dataset_ids or [
            value for value in (default_dataset_ids or []) if value
        ]
        if not effective_dataset_ids:
            missing.append("knowledge_base_binding")
        if KNOWLEDGE_BASE_TOOL not in tool_names:
            missing.append("knowledge_base_tool")

    return AgentReadiness(ready=not missing, missing=tuple(missing))
