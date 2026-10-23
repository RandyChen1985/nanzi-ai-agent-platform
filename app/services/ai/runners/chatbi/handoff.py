"""Seamless handoff from ChatBI to the platform's routed assistant."""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List

logger = logging.getLogger(__name__)


async def stream_to_routed_assistant(
    runner: Any,
    *,
    history: List[Dict[str, str]],
    user_question: str,
    reason: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Route and execute the same request without asking the user to re-submit it."""
    from app.services.ai.context_manager import AgentContextManager
    from app.services.ai.dispatcher import AgentDispatcher
    from app.services.ai.turn_decision import TurnDecision

    messages = list(history or [])
    if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != user_question:
        messages.append({"role": "user", "content": user_question})

    config, route_details = await AgentContextManager.resolve_agent_config(
        messages,
        enable_multi_agent=False,
        user_info=runner.user_info,
    )
    if not config or "data_query" in (config.capabilities or []):
        raise RuntimeError("semantic handoff did not resolve a non-data assistant")

    source_config = getattr(runner, "config", None)
    from_name = runner._runtime_agent_name()
    from_display_name = str(
        getattr(source_config, "agent_display_name", None)
        or getattr(source_config, "agent_name", None)
        or from_name
    )
    to_name = str(getattr(config, "agent_name", None) or "assistant")
    to_display_name = str(getattr(config, "agent_display_name", None) or to_name)
    yield {
        "type": "agent_handoff",
        "data": {
            "version": 1,
            "from_agent": from_name,
            "from_display_name": from_display_name,
            "to_agent": to_name,
            "to_display_name": to_display_name,
            "reason_label": str(reason or "请求类型更适合由该智能体处理")[:120],
        },
    }
    yield {
        "type": "log",
        "id": f"chatbi_handoff_{runner.trace_id[-8:]}",
        "title": "已转交合适的智能体继续处理",
        "details": reason,
        "status": "success",
        "category": "intent",
    }
    executor = await AgentDispatcher.dispatch(
        config,
        user_question,
        messages,
        runner.trace_id,
        runner.trace_buffer,
        runner.execution_options,
        runner.permission_options,
        runner.user_info,
        runner.conversation_id,
        turn_decision=(
            route_details
            if isinstance(route_details, TurnDecision)
            else TurnDecision.for_direct_agent_selection(config)
        ),
    )
    async for chunk in executor.execute(messages):
        yield chunk
    runner.step_counter = max(runner.step_counter, executor.step_counter)
