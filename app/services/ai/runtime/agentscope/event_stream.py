from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Protocol

EXTERNAL_EXECUTION_UNAVAILABLE_CHUNK: Dict[str, Any] = {
    "type": "error",
    "status": "error",
    "content": "当前版本尚未开放 AgentScope external execution 恢复接口，请改用 ASK 权限工具或平台托管工具执行。",
}


class PendingInterruptHost(Protocol):
    trace_id: str
    conversation_id: str | None

    def _runtime_user_id(self) -> str | None: ...

    def _runtime_agent_name(self) -> str: ...

    def _runner_context(self, *, system_content: str, max_steps: int) -> Dict[str, Any]: ...


def new_native_stream_state(
    *,
    system_content: str = "",
    max_steps: int = 5,
) -> Dict[str, Any]:
    import time

    return {
        "tool_names": {},
        "tool_args_text": {},
        "tool_outputs": {},
        "tool_data": {},
        "tool_started_at": {},
        "content_emitted": False,
        "used_tools": False,
        "synthesis_log_emitted": False,
        "full_content": "",
        "start_synthesis": time.time(),
        "synthesis_recorded": False,
        "system_content": system_content,
        "max_steps": max_steps,
    }


async def stream_pending_tool_interrupt(
    *,
    event: Any,
    agent: Any,
    runner: PendingInterruptHost,
    tools: List[Any],
    native_model: Any,
    state: Dict[str, Any],
    kind: str,
    sse_type: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )

    for tool_call in getattr(event, "tool_calls", []) or []:
        tool_id = getattr(tool_call, "id", "") or f"call_{uuid.uuid4().hex[:8]}"
        tool_name = getattr(tool_call, "name", "")
        raw_args = getattr(tool_call, "input", "") or "{}"
        try:
            tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            tool_args = {"input": raw_args}
        if not isinstance(tool_args, dict):
            tool_args = {"input": tool_args}
        pending = await pending_agentscope_confirmations.register(
            kind=kind,
            agent=agent,
            runner=runner,
            tools=tools,
            native_model=native_model,
            tool_call=tool_call,
            reply_id=str(getattr(event, "reply_id", "")),
            trace_id=runner.trace_id,
            user_id=runner._runtime_user_id(),
            conversation_id=runner.conversation_id,
            agent_name=runner._runtime_agent_name(),
            state=state,
            runner_context=runner._runner_context(
                system_content=state.get("system_content", ""),
                max_steps=int(state.get("max_steps", 5)),
            ),
        )
        yield {
            "type": sse_type,
            "status": "pending",
            "id": tool_id,
            "permission_request_id": pending.request_id,
            "reply_id": pending.reply_id,
            "expires_in_seconds": 600,
            "title": (
                f"需要确认工具调用: {tool_name}"
                if kind == "permission"
                else f"需要外部执行工具: {tool_name}"
            ),
            "details": f"参数: {json.dumps(tool_args, ensure_ascii=False)}",
            "tool_call": {
                "id": tool_id,
                "name": tool_name,
                "args": tool_args,
            },
        }


def map_tool_result_data_delta(
    event: Any,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    tool_id = getattr(event, "tool_call_id", "")
    payload = {
        "block_id": getattr(event, "block_id", ""),
        "media_type": getattr(event, "media_type", ""),
        "data": getattr(event, "data", None),
        "url": getattr(event, "url", None),
    }
    tool_data = state.setdefault("tool_data", {})
    tool_data.setdefault(tool_id, []).append(payload)
    return {
        "type": "tool_result_data",
        "tool_call_id": tool_id,
        **payload,
    }


async def map_standard_agentscope_event(
    event: Any,
    *,
    state: Dict[str, Any],
    on_tool_result_end: Callable[[Any], AsyncGenerator[Dict[str, Any], None]] | None = None,
    on_text_block_delta: Callable[[Any], AsyncGenerator[Dict[str, Any], None]] | None = None,
    agent: Any | None = None,
    runner: PendingInterruptHost | None = None,
    tools: List[Any] | None = None,
    native_model: Any | None = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Map common AgentScope events to SSE chunks; delegate specialized hooks."""
    import time

    event_type = str(getattr(event, "type", ""))
    if event_type == "THINKING_BLOCK_DELTA":
        yield {"type": "thinking", "status": "continuing"}
        return

    if event_type == "TOOL_CALL_START":
        state["used_tools"] = True
        tool_id = getattr(event, "tool_call_id", "")
        tool_name = getattr(event, "tool_call_name", "")
        tool_names = state.setdefault("tool_names", {})
        tool_started_at = state.setdefault("tool_started_at", {})
        tool_names[tool_id] = tool_name
        tool_started_at[tool_id] = time.time()
        yield {
            "type": "log",
            "id": tool_id,
            "title": f"调用工具: {tool_name}",
            "details": "参数: {}",
            "status": "pending",
        }
        return

    if event_type == "TOOL_CALL_DELTA":
        tool_id = getattr(event, "tool_call_id", "")
        tool_args_text = state.setdefault("tool_args_text", {})
        tool_args_text[tool_id] = tool_args_text.get(tool_id, "") + str(getattr(event, "delta", ""))
        return

    if event_type == "TOOL_RESULT_TEXT_DELTA":
        tool_id = getattr(event, "tool_call_id", "")
        tool_outputs = state.setdefault("tool_outputs", {})
        tool_outputs[tool_id] = tool_outputs.get(tool_id, "") + str(getattr(event, "delta", ""))
        return

    if event_type == "TOOL_RESULT_DATA_DELTA":
        yield map_tool_result_data_delta(event, state)
        return

    if event_type == "TOOL_RESULT_END":
        if on_tool_result_end is not None:
            async for chunk in on_tool_result_end(event):
                yield chunk
        return

    if event_type == "REQUIRE_EXTERNAL_EXECUTION":
        yield EXTERNAL_EXECUTION_UNAVAILABLE_CHUNK
        return

    if event_type == "REQUIRE_USER_CONFIRM":
        if agent is not None and runner is not None and tools is not None and native_model is not None:
            async for chunk in stream_pending_tool_interrupt(
                event=event,
                agent=agent,
                runner=runner,
                tools=tools,
                native_model=native_model,
                state=state,
                kind="permission",
                sse_type="permission_required",
            ):
                yield chunk
        return

    if event_type == "TEXT_BLOCK_DELTA":
        if on_text_block_delta is not None:
            async for chunk in on_text_block_delta(event):
                yield chunk
        return

    if event_type == "EXCEED_MAX_ITERS":
        from app.services.ai.executors.prompts import GeneralChatPrompts

        yield {"content": GeneralChatPrompts.MAX_STEPS_REACHED}
        return
