from __future__ import annotations

from typing import Any, Callable, Iterable

from app.services.ai.runtime.agentscope.errors import RuntimeConfigurationError
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.tool_capability import (
    AgentScopeToolConsumer,
    RegistryToolProvider,
    ResolvedToolSet,
    resolve_tool_capabilities,
)
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
        if isinstance(item, dict) and item.get("enabled", True) is False:
            continue
        if not isinstance(item, (str, dict)) and getattr(item, "enabled", True) is False:
            continue
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
    *,
    implicit_tools: Iterable[Any] | None = None,
    on_resolved: Callable[[ResolvedToolSet], None] | None = None,
    agent_timeout: Any = None,
) -> list[RuntimeToolSpec]:
    configured_items = list(tool_configs) if tool_configs else list(CHATBI_DEFAULT_TOOL_NAMES)
    resolved = await resolve_tool_capabilities(
        configured_items,
        implicit_tools=implicit_tools,
        required_names=CHATBI_REQUIRED_TOOL_NAMES,
        provider=RegistryToolProvider(registry=ToolRegistry),
    )
    if on_resolved is not None:
        on_resolved(resolved)
    if resolved.missing_required:
        raise RuntimeConfigurationError(
            "missing required ChatBI runtime tools",
            details={"missing_tools": list(resolved.missing_required)},
        )
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_configured_agent_tool_timeout,
        collect_tool_timeouts,
    )

    return await apply_configured_agent_tool_timeout(
        resolved.specs,
        agent_timeout=agent_timeout,
        # ChatBI 也是从版本 tools 装配工具，工具级超时同样要按名字命中。
        tool_timeouts=collect_tool_timeouts(configured_items),
    )


async def build_chatbi_toolkit(
    tool_configs: Iterable[Any] | None,
    *,
    implicit_tools: Iterable[Any] | None = None,
    on_resolved: Callable[[ResolvedToolSet], None] | None = None,
    agent_timeout: Any = None,
) -> tuple[Any, list[RuntimeToolSpec]]:
    specs = await resolve_chatbi_runtime_tools(
        tool_configs,
        implicit_tools=implicit_tools,
        on_resolved=on_resolved,
        agent_timeout=agent_timeout,
    )
    toolkit = AgentScopeToolConsumer().consume_specs(specs)
    return toolkit, specs
