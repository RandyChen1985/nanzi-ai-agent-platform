"""ChatBI suspended AgentScope stream resume."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List

from app.services.ai.config import AgentConfigProvider
from app.services.ai.runtime.agentscope.agent_runtime import build_tools_fingerprint
from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk
from app.services.ai.runtime.agentscope.session_lock import SessionLockTimeout, agentscope_session_lock
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runners.chatbi import agent_builder as chatbi_agent_builder
from app.services.ai.runners.chatbi import state_serialization as chatbi_state
from app.services.ai.runners.chatbi.run_state import DataRunState


async def resolve_pending_runtime(
    runner: Any,
    pending: Any,
) -> tuple[Any, list[RuntimeToolSpec], Any, DataRunState, Dict[str, Any]]:
    if pending.agent is not None and pending.tools and pending.native_model is not None:
        data_state, stream_meta = chatbi_state.pending_state_to_data_run_state(pending.state or {})
        guarded_tools = runner._wrap_tools_with_schema_gate(pending.tools, data_state)
        return pending.agent, guarded_tools, pending.native_model, data_state, stream_meta

    ctx = pending.snapshot.runner_context
    tools = await chatbi_agent_builder.resolve_runtime_tools_from_config(runner)
    native_model_handle = await AgentConfigProvider.get_configured_llm(
        streaming=True,
        config=runner.config,
    )
    # 与首轮一致：恢复后重建的轨迹同样要报真实下发的模型。
    runner._record_effective_model(native_model_handle)
    native_model = getattr(native_model_handle, "native_model", None)
    if native_model is None:
        raise RuntimeError("当前模型适配器未提供 AgentScope native_model，无法恢复挂起执行。")
    from agentscope.state import AgentState

    restored_state = AgentState.model_validate(pending.snapshot.agent_state)
    data_state, stream_meta = chatbi_state.pending_state_to_data_run_state(
        pending.state or dict(pending.snapshot.stream_state or {})
    )
    guarded_tools = runner._wrap_tools_with_schema_gate(tools, data_state)
    agent = await chatbi_agent_builder.build_native_agent(
        runner,
        native_model=native_model,
        tools=guarded_tools,
        system_content=str(ctx.get("system_content", "")),
        max_steps=int(ctx.get("max_steps", 5)),
        restored_state=restored_state,
        primary_model_name=str(getattr(native_model, "model", runner.config.model_name) or ""),
    )
    return agent, guarded_tools, native_model, data_state, stream_meta


async def resume_agentscope_native_stream(
    runner: Any,
    *,
    pending: Any,
    resume_event: Any,
) -> AsyncGenerator[Dict[str, Any], None]:
    if hasattr(runner, "_ensure_agent_context"):
        runner._ensure_agent_context()
    agent_name = runner._runtime_agent_name()
    try:
        async with agentscope_session_lock.hold(
            user_id=runner._runtime_user_id(),
            conversation_id=runner.conversation_id,
            agent_name=agent_name,
            ttl_seconds=300,
        ):
            agent, tools, native_model, data_state, stream_meta = await resolve_pending_runtime(
                runner,
                pending,
            )
            interrupted = False
            async for chunk in runner._stream_agentscope_events(
                event_stream=agent.reply_stream(resume_event),
                agent=agent,
                tools=tools,
                native_model=native_model,
                state=data_state,
                stream_meta=stream_meta,
                emit_final_guard=True,
            ):
                if is_interrupt_sse_chunk(chunk):
                    interrupted = True
                yield chunk
            if not interrupted and runner.conversation_id:
                tools_fingerprint = build_tools_fingerprint(runner.config, tools)
                await agent_state_store.save(
                    user_id=runner._runtime_user_id(),
                    conversation_id=runner.conversation_id,
                    agent_name=agent_name,
                    agent_version=runner.config.agent_version,
                    tools_fingerprint=tools_fingerprint,
                    model_name=str(getattr(native_model, "model", runner.config.model_name) or ""),
                    state=agent.state,
                )
    except SessionLockTimeout:
        yield {
            "type": "error",
            "status": "error",
            "content": "当前会话正在处理中，请稍后再试。",
        }


async def resume_agentscope_native_confirmation(
    runner: Any,
    pending: Any,
    *,
    confirmed: bool,
) -> AsyncGenerator[Dict[str, Any], None]:
    from agentscope.event import ConfirmResult, UserConfirmResultEvent

    event = UserConfirmResultEvent(
        reply_id=pending.reply_id,
        confirm_results=[ConfirmResult(confirmed=confirmed, tool_call=pending.tool_call)],
    )
    async for chunk in resume_agentscope_native_stream(
        runner,
        pending=pending,
        resume_event=event,
    ):
        yield chunk


async def resume_agentscope_external_execution(
    runner: Any,
    pending: Any,
    *,
    execution_results: List[Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    from agentscope.event import ExternalExecutionResultEvent

    event = ExternalExecutionResultEvent(
        reply_id=pending.reply_id,
        execution_results=execution_results,
    )
    async for chunk in resume_agentscope_native_stream(
        runner,
        pending=pending,
        resume_event=event,
    ):
        yield chunk
