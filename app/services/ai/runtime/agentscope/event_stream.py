from __future__ import annotations

import json
import logging
import asyncio
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Protocol

from app.services.ai.context_compaction_log_service import context_compaction_log_service
from app.services.ai.runtime.agentscope.tool_result import normalize_tool_result_state

logger = logging.getLogger(__name__)


def _get_env_once():
    from app.utils.env import get_env

    return get_env()


async def _sandbox_bash_env(state: Dict[str, Any] | None = None) -> str:
    """计算上报前端的 Bash 实际执行环境。

    Agent runner 在工具绑定成功后把实际后端写入 stream state。只要 state
    可用，就不再根据配置策略猜测；旧的直接调用方才保留配置回退。
    """
    if state is not None:
        execution_backend = str(state.get("execution_backend") or "").strip().lower()
        if execution_backend in {"host", "docker", "e2b", "ssh"}:
            return execution_backend
        return _get_env_once()

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", "local"),
    )
    if policy in ("docker", "e2b", "ssh"):
        return policy
    return _get_env_once()


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
        "tool_result_states": {},
        "tool_data": {},
        "inline_tool_argument_ids": {},
        "tool_started_at": {},
        "content_emitted": False,
        "used_tools": False,
        "synthesis_log_emitted": False,
        "bash_env_emitted": False,
        "full_content": "",
        "pending_reply_text": "",
        "pending_reply_emitted": False,
        "process_narration": "",
        "reply_phase": "before_tool",
        "current_reply_used_tools": False,
        "current_reply_tool_names": [],
        "start_synthesis": time.time(),
        "synthesis_recorded": False,
        "system_content": system_content,
        "max_steps": max_steps,
        "model_call_started_at": {},
        "model_fallback_notice_emitted": False,
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


def _model_fallback_notice_event(
    agent: Any | None,
    state: Dict[str, Any],
) -> Dict[str, Any] | None:
    if state.get("model_fallback_notice_emitted"):
        return None
    info = getattr(agent, "_platform_fallback_info", None) if agent is not None else None
    if not isinstance(info, dict):
        return None

    primary_model = str(info.get("primary_model") or "unknown")
    fallback_model = str(info.get("fallback_model") or "unknown")
    state["model_fallback_notice_emitted"] = True
    return {
        "type": "model_fallback",
        "status": "warning",
        "primary_model": primary_model,
        "fallback_model": fallback_model,
        "content": (
            f"> ⚠️ 主模型 `{primary_model}` 调用失败，"
            f"本次回答由 fallback 模型 `{fallback_model}` 生成。\n\n"
        ),
    }


def is_interrupt_sse_chunk(chunk: Dict[str, Any]) -> bool:
    """Native agent 循环是否应暂停并等待外部恢复。

    工具执行日志（type=log）在失败时也会带 status=error，但不应中断循环，
    否则 reconcile / synthesis 兜底无法向用户输出可见正文。
    """
    return chunk.get("type") in {
        "permission_required",
        "external_execution_required",
        "user_question",
        "error",
    }


def _pending_request_id_field(kind: str) -> str:
    return "permission_request_id" if kind == "permission" else "external_execution_request_id"


def _sync_todo_snapshot_from_context(state: Dict[str, Any], context: Any) -> None:
    """在挂起快照注册前复制当前轮 Todo，避免恢复时丢失清单。"""
    snapshot = getattr(context, "todo_snapshot", None) if context is not None else None
    if isinstance(snapshot, dict):
        state["todo_snapshot"] = dict(snapshot)


def _sync_published_download_urls_from_context(state: Dict[str, Any], context: Any) -> None:
    """Copy issued download URLs into a pending snapshot before an interrupt."""
    urls = getattr(context, "published_download_urls", None) if context is not None else None
    if not isinstance(urls, list):
        return
    existing = state.get("published_download_urls")
    combined = list(existing) if isinstance(existing, list) else []
    for url in urls:
        if str(url) and url not in combined:
            combined.append(url)
    state["published_download_urls"] = combined


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
    from app.core.context import get_current_agent_context

    def _resolve_tool_display_name(target_name: str, *containers: Any) -> str:
        for container in containers:
            if not container:
                continue
            if isinstance(container, dict):
                matched = container.get(target_name)
                if matched:
                    d_name = getattr(matched, "display_name", None)
                    if d_name:
                        return str(d_name)
            elif isinstance(container, (list, tuple, set)):
                for t in container:
                    if getattr(t, "name", None) == target_name:
                        d_name = getattr(t, "display_name", None)
                        if d_name:
                            return str(d_name)
        return target_name

    request_id_field = _pending_request_id_field(kind)
    for tool_call in getattr(event, "tool_calls", []) or []:
        tool_id = getattr(tool_call, "id", "") or f"call_{uuid.uuid4().hex[:8]}"
        tool_name = getattr(tool_call, "name", "")
        display_name = _resolve_tool_display_name(tool_name, tools, getattr(runner, "tools", None))

        raw_args = getattr(tool_call, "input", "") or "{}"
        try:
            tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            tool_args = {"input": raw_args}
        if not isinstance(tool_args, dict):
            tool_args = {"input": tool_args}
        if tool_name == "browser_fill":
            from app.services.ai.browser.browser_policy import redact_browser_arguments

            tool_args = redact_browser_arguments({**tool_args, "sensitive": True})
        _sync_todo_snapshot_from_context(state, get_current_agent_context())
        _sync_published_download_urls_from_context(state, get_current_agent_context())
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
                f"需要确认工具调用: {display_name}"
                if kind == "permission"
                else f"需要外部执行工具: {display_name}"
            ),
            "details": f"参数: {json.dumps(tool_args, ensure_ascii=False)}",
            "tool_call": {
                "id": tool_id,
                "name": tool_name,
                "display_name": display_name,
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

    fallback_notice = _model_fallback_notice_event(agent, state)
    if fallback_notice:
        yield fallback_notice

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


async def _persist_context_compression_event(
    event: Dict[str, Any],
    *,
    runner: PendingInterruptHost | None,
    agent_name: str | None,
) -> None:
    """在 AgentScope SSE 映射边界记录压缩事件，失败时不影响流。"""
    if runner is None or not getattr(runner, "conversation_id", None):
        return
    user_id_getter = getattr(runner, "_runtime_user_id", None)
    user_id = user_id_getter() if callable(user_id_getter) else None
    if not user_id:
        return
    try:
        await asyncio.wait_for(
            context_compaction_log_service.append_event(
                event=event,
                user_id=user_id,
                conversation_id=runner.conversation_id,
                trace_id=getattr(runner, "trace_id", None),
                source="agentscope",
                stage="agent_runtime",
                agent_name=agent_name,
            ),
            timeout=context_compaction_log_service.APPEND_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning(
            "[AgentScope] Failed to persist context compression event",
            exc_info=True,
        )


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
            if chunk.get("type") == "context_compression":
                await _persist_context_compression_event(
                    chunk,
                    runner=runner,
                    agent_name=agent_name,
                )
            yield chunk

    event_type = str(getattr(event, "type", ""))
    if event_type == "THINKING_BLOCK_DELTA":
        yield {"type": "thinking", "status": "continuing"}
        delta = str(getattr(event, "delta", "") or "")
        if delta:
            yield {"type": "reasoning_content", "content": delta}
        return

    if event_type == "TOOL_CALL_START":
        state["used_tools"] = True
        tool_id = getattr(event, "tool_call_id", "")
        tool_name = getattr(event, "tool_call_name", "")
        tool_names = state.setdefault("tool_names", {})
        inline_arguments = getattr(event, "arguments", None)
        if inline_arguments is None:
            inline_arguments = getattr(event, "input", None)
        if inline_arguments is not None:
            state.setdefault("tool_args_text", {})[tool_id] = (
                json.dumps(inline_arguments, ensure_ascii=False)
                if isinstance(inline_arguments, (dict, list))
                else str(inline_arguments)
            )
            inline_argument_ids = state.get("inline_tool_argument_ids")
            if not isinstance(inline_argument_ids, dict):
                inline_argument_ids = {
                    str(item): True
                    for item in inline_argument_ids
                } if isinstance(inline_argument_ids, (list, tuple, set)) else {}
                state["inline_tool_argument_ids"] = inline_argument_ids
            inline_argument_ids[tool_id] = True
        tool_started_at = state.setdefault("tool_started_at", {})
        tool_names[tool_id] = tool_name
        tool_started_at[tool_id] = time.time()

        # 方案二：工具白名单校验（硬拦截幻觉工具调用）
        # tools 在 Simple Mode 下为 None，跳过校验；ReAct Mode 下必须校验。
        if tools is not None:
            known_tool_names = {getattr(t, "name", "") for t in tools}
            if tool_name and tool_name not in known_tool_names:
                logger.warning(
                    "[ToolGuard] LLM attempted to call unregistered tool '%s' (tool_id=%s). "
                    "Marking as ghost and injecting error feedback.",
                    tool_name,
                    tool_id,
                )
                ghost_tool_ids = state.setdefault("ghost_tool_ids", set())
                ghost_tool_ids.add(tool_id)
                yield {
                    "type": "log",
                    "id": tool_id,
                    "title": f"⚠️ 未知工具调用被拦截: {tool_name}",
                    "details": (
                        f"模型尝试调用工具 `{tool_name}`，但该工具未在本智能体当前会话中注册。"
                        f"已拦截，将向模型注入错误反馈以纠正。"
                    ),
                    "status": "error",
                }
                return

        yield {
            "type": "log",
            "id": tool_id,
            "title": f"调用工具: {tool_name}",
            "details": "参数: {}",
            "status": "pending",
        }
        if tool_name == "Bash" and not state.get("bash_env_emitted"):
            state["bash_env_emitted"] = True
            yield {"type": "bash_env", "env": await _sandbox_bash_env(state)}
        return

    if event_type == "TOOL_CALL_DELTA":
        tool_id = getattr(event, "tool_call_id", "")
        inline_argument_ids = state.get("inline_tool_argument_ids") or {}
        if (
            isinstance(inline_argument_ids, dict)
            and inline_argument_ids.get(tool_id)
        ) or (
            isinstance(inline_argument_ids, (list, tuple, set))
            and tool_id in inline_argument_ids
        ):
            return
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
        tool_id = getattr(event, "tool_call_id", "")
        result_state = getattr(event, "state", None)
        if tool_id and result_state is not None:
            state.setdefault("tool_result_states", {})[tool_id] = normalize_tool_result_state(
                result_state
            )
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
