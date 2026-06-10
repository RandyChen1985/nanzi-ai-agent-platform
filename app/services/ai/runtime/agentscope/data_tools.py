from __future__ import annotations

from typing import Any, Iterable

from app.services.ai.runtime.agentscope.errors import RuntimeConfigurationError
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit
from app.services.ai.tools.registry import ToolRegistry


CHATBI_DEFAULT_TOOL_NAMES = (
    "get_dataset_schema",
    "execute_sql_query",
    "update_dashboard_context",
)
CHATBI_REQUIRED_TOOL_NAMES = (
    "get_dataset_schema",
    "execute_sql_query",
)


def resolve_chatbi_tool_names(tool_configs: Iterable[Any] | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str | None) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        names.append(name)

    for item in tool_configs or CHATBI_DEFAULT_TOOL_NAMES:
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            add(item.get("name"))
        else:
            add(getattr(item, "name", None))

    for required_name in CHATBI_REQUIRED_TOOL_NAMES:
        add(required_name)
    return names


async def resolve_chatbi_runtime_tools(
    tool_configs: Iterable[Any] | None,
) -> list[RuntimeToolSpec]:
    tool_names = resolve_chatbi_tool_names(tool_configs)
    specs = await ToolRegistry.get_runtime_tools(tool_names)
    resolved_names = {spec.name for spec in specs}
    missing_required = [
        name for name in CHATBI_REQUIRED_TOOL_NAMES if name not in resolved_names
    ]
    if missing_required:
        raise RuntimeConfigurationError(
            "missing required ChatBI runtime tools",
            details={"missing_tools": missing_required},
        )
    return specs


async def build_chatbi_toolkit(
    tool_configs: Iterable[Any] | None,
) -> tuple[Any, list[RuntimeToolSpec]]:
    specs = await resolve_chatbi_runtime_tools(tool_configs)
    return build_toolkit(specs), specs
