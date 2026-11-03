"""ChatBI AgentScope agent construction and tool resolution."""

from __future__ import annotations

from typing import Any, Dict

from app.services.ai.runtime.agentscope.build_progress import (
    BuildStageTimer,
    emit_build_progress,
    format_build_details,
)
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
)
from app.services.ai.tool_capability import AgentScopeToolConsumer
from app.services.ai.tools.registry import ToolRegistry


def _runner_module():
    """Lazy import avoids circular dependency; tests patch symbols on this module."""
    from app.services.ai.runners import data_agent_runner

    return data_agent_runner


async def resolve_runtime_tools_from_config(runner: Any) -> list[RuntimeToolSpec]:
    dar = _runner_module()
    system_tools = ToolRegistry.get_system_implicit_tools()
    runner._last_tool_resolution = None

    def capture_resolution(resolved: Any) -> None:
        runner._last_tool_resolution = resolved

    _, specs = await dar.build_chatbi_toolkit(
        runner.config.tools,
        implicit_tools=system_tools,
        on_resolved=capture_resolution,
        agent_timeout=getattr(runner.config, "toolcall_timeout_seconds", None),
    )
    return list(specs)


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
    from app.services.ai.time_anchor import filter_redundant_time_tools

    dar = _runner_module()
    build_timer = BuildStageTimer()
    await emit_build_progress(
        status="pending",
        details="正在装配工作区、工具与会话状态…",
    )
    tools = filter_redundant_time_tools(tools, system_content)
    workspace = await dar.get_local_workspace(
        user_id=runner._current_user_id(),
        user_name=runner._runtime_user_name(),
        user_info=runner.user_info,
        conversation_id=runner.conversation_id,
        skills_custom=bool(getattr(runner.config, "skills_custom", False)),
        allowed_global_skills=list(getattr(runner.config, "skills", None) or []),
        lazy_sandbox=True,
    )
    build_timer.mark("workspace")
    from app.services.ai.runtime.agentscope.workspace import (
        bind_configured_tools_to_workspace,
        get_workspace_execution_backend,
        get_workspace_offloader,
    )

    tools = await bind_configured_tools_to_workspace(
        workspace,
        tools,
        user_info=runner.user_info,
    )
    build_timer.mark("tool_bind")
    runner._execution_backend = get_workspace_execution_backend(workspace)
    toolkit = AgentScopeToolConsumer(builder=dar.build_toolkit).consume_specs(
        tools,
        approval_mode=runner.permission_options.get("approval_mode"),
        user_id=runner._current_user_id(),
        conversation_id=runner.conversation_id,
    )
    build_timer.mark("toolkit")
    from app.services.ai.runtime.agentscope.agent_runtime import (
        build_runtime_middlewares,
        load_injection_config,
    )

    context_config = await dar.load_context_config()
    model_config = await dar.build_model_config(
        config=runner.config,
        primary_model_name=primary_model_name,
    )
    injection_config = await load_injection_config()
    build_timer.mark("config")
    middlewares = build_runtime_middlewares(
        user_id=runner._current_user_id(),
        conversation_id=runner.conversation_id,
        agent_name=runner._runtime_agent_name(),
        trace_id=runner.trace_id,
    )
    build_timer.mark("middleware")
    kwargs: Dict[str, Any] = {
        "name": runner._runtime_agent_name(),
        "system_prompt": system_content,
        "model": native_model,
        "toolkit": toolkit,
        "react_config": dar.ReActConfig(max_iters=max_steps),
        "middlewares": middlewares,
        "injection_config": injection_config,
    }
    if restored_state is not None:
        kwargs["state"] = restored_state
    if workspace is not None:
        kwargs["offloader"] = get_workspace_offloader(workspace)
    if model_config is not None:
        kwargs["model_config"] = model_config
    if context_config is not None:
        kwargs["context_config"] = context_config
    agent = dar.Agent(**kwargs)
    build_timer.mark("agent")
    total_ms = build_timer.elapsed_ms()
    await emit_build_progress(
        status="success",
        details=format_build_details(build_timer.stages(), total_ms),
        execution_time_ms=total_ms,
    )
    return agent
