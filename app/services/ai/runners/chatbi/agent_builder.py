"""ChatBI AgentScope agent construction and tool resolution."""

from __future__ import annotations

from typing import Any, Dict, List

from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, runtime_tool_spec_from_legacy_tool
from app.services.ai.tools.registry import ToolRegistry


def _runner_module():
    """Lazy import avoids circular dependency; tests patch symbols on this module."""
    from app.services.ai.runners import data_agent_runner

    return data_agent_runner


async def resolve_runtime_tools_from_config(runner: Any) -> list[RuntimeToolSpec]:
    dar = _runner_module()
    _, specs = await dar.build_chatbi_toolkit(runner.config.tools)
    tools = list(specs)
    seen = {spec.name for spec in tools}
    system_tools = ToolRegistry.get_system_implicit_tools()
    if system_tools:
        for tool in system_tools:
            spec = runtime_tool_spec_from_legacy_tool(tool, source_type="system")
            if spec.name in seen:
                continue
            tools.append(spec)
            seen.add(spec.name)

    return tools


async def build_native_agent(
    runner: Any,
    *,
    native_model: Any,
    tools: list[RuntimeToolSpec],
    system_content: str,
    max_steps: int,
    primary_model_name: str,
    restored_state: Any = None,
) -> Any:
    dar = _runner_module()
    toolkit = dar.build_toolkit(tools, approval_mode=runner.permission_options.get("approval_mode"), user_id=runner._current_user_id())
    workspace = await dar.get_local_workspace(
        user_id=runner._current_user_id(),
        user_name=runner._runtime_user_name(),
        user_info=runner.user_info,
        conversation_id=runner.conversation_id,
    )
    context_config = await dar.load_context_config()
    model_config = await dar.build_model_config(
        config=runner.config,
        primary_model_name=primary_model_name,
    )
    middlewares = []
    if runner.conversation_id:
        from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

        middlewares.append(
            ModelCallStatsMiddleware(
                user_id=runner._current_user_id(),
                conversation_id=runner.conversation_id,
                agent_name=runner._runtime_agent_name(),
                trace_id=runner.trace_id,
            )
        )
    kwargs: Dict[str, Any] = {
        "name": runner._runtime_agent_name(),
        "system_prompt": system_content,
        "model": native_model,
        "toolkit": toolkit,
        "react_config": dar.ReActConfig(max_iters=max_steps),
        "middlewares": middlewares,
    }
    if restored_state is not None:
        kwargs["state"] = restored_state
    if workspace is not None:
        kwargs["offloader"] = workspace
    if model_config is not None:
        kwargs["model_config"] = model_config
    if context_config is not None:
        kwargs["context_config"] = context_config
    return dar.Agent(**kwargs)
