from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Protocol

logger = logging.getLogger(__name__)


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
        "model_call_started_at": {},
        "_observed_summary_len": 0,
    }


def extract_latest_assistant_text(agent: Any, *, include_thinking: bool = False) -> str:
    """从 AgentState.context 提取最近一条 assistant 可展示文本（流式未发 TEXT_BLOCK_DELTA 时兜底）。"""
    from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text

    agent_state = getattr(agent, "state", None)
    context = getattr(agent_state, "context", None) or []
    block_types = ("text", "thinking") if include_thinking else ("text",)
    for msg in reversed(context):
        if getattr(msg, "role", None) != "assistant":
            continue
        get_blocks = getattr(msg, "get_content_blocks", None)
        if not callable(get_blocks):
            continue
        parts: list[str] = []
        for block_type in block_types:
            try:
                blocks = get_blocks(block_type)
            except Exception:
                blocks = []
            for block in blocks or []:
                text = str(getattr(block, "text", "") or "")
                if text.strip():
                    parts.append(text)
        if parts:
            cleaned = sanitize_assistant_stream_text("".join(parts))
            if cleaned.strip():
                return cleaned
    return ""


def is_interrupt_sse_chunk(chunk: Dict[str, Any]) -> bool:
    return chunk.get("type") in {
        "permission_required",
        "external_execution_required",
    } or chunk.get("status") == "error"


def _pending_request_id_field(kind: str) -> str:
    return "permission_request_id" if kind == "permission" else "external_execution_request_id"


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

    request_id_field = _pending_request_id_field(kind)
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
            request_id_field: pending.request_id,
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


def maybe_emit_context_compression(
    *,
    agent: Any | None,
    state: Dict[str, Any],
    agent_name: str | None = None,
) -> Dict[str, Any] | None:
    if agent is None:
        return None
    agent_state = getattr(agent, "state", None)
    summary = getattr(agent_state, "summary", None) or ""
    prev_len = int(state.get("_observed_summary_len", 0) or 0)
    current_len = len(summary)
    state["_observed_summary_len"] = current_len
    if current_len <= prev_len or current_len == 0:
        return None
    logger.info(
        "[AgentScope] Context compressed agent=%s summary_chars=%d",
        agent_name or getattr(agent, "name", "unknown"),
        current_len,
    )
    preview = summary[:400] + ("..." if len(summary) > 400 else "")
    return {
        "type": "context_compression",
        "title": "上下文已压缩",
        "details": preview,
        "summary_chars": current_len,
        "status": "success",
    }


async def stream_observability_agentscope_events(
    event: Any,
    *,
    state: Dict[str, Any],
    agent: Any | None = None,
    agent_name: str | None = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    event_type = str(getattr(event, "type", ""))

    if event_type == "REPLY_START":
        yield {
            "type": "agent_reply",
            "phase": "start",
            "reply_id": getattr(event, "reply_id", ""),
            "session_id": getattr(event, "session_id", ""),
            "agent_name": getattr(event, "name", agent_name or ""),
        }
        return

    if event_type == "REPLY_END":
        yield {
            "type": "agent_reply",
            "phase": "end",
            "reply_id": getattr(event, "reply_id", ""),
            "session_id": getattr(event, "session_id", ""),
        }
        return

    if event_type == "MODEL_CALL_START":
        reply_id = getattr(event, "reply_id", "")
        state.setdefault("model_call_started_at", {})[reply_id] = time.time()
        yield {
            "type": "model_call",
            "phase": "start",
            "reply_id": reply_id,
            "model_name": getattr(event, "model_name", ""),
        }
        return

    if event_type == "MODEL_CALL_END":
        reply_id = getattr(event, "reply_id", "")
        started_at = state.get("model_call_started_at", {}).get(reply_id, time.time())
        input_tokens = int(getattr(event, "input_tokens", 0) or 0)
        output_tokens = int(getattr(event, "output_tokens", 0) or 0)
        duration_ms = (time.time() - started_at) * 1000
        logger.info(
            "[AgentScope] model_call_end agent=%s reply_id=%s model=%s input_tokens=%d output_tokens=%d duration_ms=%.1f",
            agent_name or "",
            reply_id,
            getattr(event, "model_name", ""),
            input_tokens,
            output_tokens,
            duration_ms,
        )
        yield {
            "type": "model_call",
            "phase": "end",
            "reply_id": reply_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
        }
        compression = maybe_emit_context_compression(
            agent=agent,
            state=state,
            agent_name=agent_name,
        )
        if compression:
            yield compression
        return

    if event_type == "THINKING_BLOCK_START":
        yield {
            "type": "thinking",
            "phase": "start",
            "block_id": getattr(event, "block_id", ""),
            "reply_id": getattr(event, "reply_id", ""),
        }
        return

    if event_type == "THINKING_BLOCK_END":
        yield {
            "type": "thinking",
            "phase": "end",
            "block_id": getattr(event, "block_id", ""),
            "reply_id": getattr(event, "reply_id", ""),
        }
        return

    if event_type == "CUSTOM":
        name = str(getattr(event, "name", "") or "")
        value = getattr(event, "value", None) or {}
        if name == "state_updated":
            logger.info(
                "[AgentScope] state_updated agent=%s payload=%s",
                agent_name or "",
                json.dumps(value, ensure_ascii=False)[:500],
            )
            yield {
                "type": "context_update",
                "name": name,
                "value": value,
                "title": "Agent 状态已更新",
                "details": json.dumps(value, ensure_ascii=False)[:500],
                "status": "success",
            }
        return


async def _maybe_await_pending_hook(
    hook: Callable[[Dict[str, Any]], Any] | None,
    state: Dict[str, Any],
) -> None:
    if hook is None:
        return
    result = hook(state)
    if hasattr(result, "__await__"):
        await result


async def map_standard_agentscope_event(
    event: Any,
    *,
    state: Dict[str, Any],
    on_tool_result_end: Callable[[Any], AsyncGenerator[Dict[str, Any], None]] | None = None,
    on_text_block_delta: Callable[[Any], AsyncGenerator[Dict[str, Any], None]] | None = None,
    on_before_pending_interrupt: Callable[[Dict[str, Any]], Any] | None = None,
    agent: Any | None = None,
    runner: PendingInterruptHost | None = None,
    tools: List[Any] | None = None,
    native_model: Any | None = None,
    agent_name: str | None = None,
    emit_observability: bool = True,
) -> AsyncGenerator[Dict[str, Any], None]:
    if emit_observability:
        async for chunk in stream_observability_agentscope_events(
            event,
            state=state,
            agent=agent,
            agent_name=agent_name,
        ):
            yield chunk

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
        compression = maybe_emit_context_compression(
            agent=agent,
            state=state,
            agent_name=agent_name,
        )
        if compression:
            yield compression
        return

    if event_type == "REQUIRE_EXTERNAL_EXECUTION":
        if agent is not None and runner is not None and tools is not None and native_model is not None:
            await _maybe_await_pending_hook(on_before_pending_interrupt, state)
            async for chunk in stream_pending_tool_interrupt(
                event=event,
                agent=agent,
                runner=runner,
                tools=tools,
                native_model=native_model,
                state=state,
                kind="external",
                sse_type="external_execution_required",
            ):
                yield chunk
        return

    if event_type == "REQUIRE_USER_CONFIRM":
        if agent is not None and runner is not None and tools is not None and native_model is not None:
            await _maybe_await_pending_hook(on_before_pending_interrupt, state)
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
        from app.services.ai.executors.prompts import AssistantPrompts

        yield {"content": AssistantPrompts.MAX_STEPS_REACHED}
        return
