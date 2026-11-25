from __future__ import annotations

import logging
from typing import Any

from app.services.ai.runtime.agentscope.session_lock import agentscope_session_lock
from app.services.ai.runtime.conversation_run_registry import conversation_run_registry
from app.services.ai.runtime.session_run_lane import conversation_run_lane

logger = logging.getLogger(__name__)


async def release_conversation_run_locks(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Force-release per-conversation run lane and AgentScope session locks.

    Used when the client aborts SSE or explicitly cancels generation so the
    user can send a new message without waiting for Redis lock TTL expiry.

    `trace_id` 不只是日志字段：它会作为 lane 锁的 compare-and-delete 令牌
    （acquire 时 `token = trace_id or uuid4().hex`）。取消后紧跟一条新消息时，
    新请求可能已在同一毫秒内抢到这把锁——盲删会把新一轮的互斥抹掉，
    两个 run 并发操作同一 AgentState。
    """
    if not conversation_id:
        return {
            "success": False,
            "lane_released": False,
            "session_locks_released": 0,
        }

    lane_released = await conversation_run_lane.force_release(
        user_id=user_id,
        conversation_id=conversation_id,
        expected_token=trace_id,
    )
    session_locks_released = await agentscope_session_lock.force_release_all_for_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
    )

    if lane_released or session_locks_released:
        logger.info(
            "[ConversationRunCancel] released locks conversation=%s user=%s trace=%s "
            "lane=%s session_locks=%s",
            conversation_id,
            user_id,
            trace_id,
            lane_released,
            session_locks_released,
        )

    return {
        "success": lane_released or session_locks_released > 0,
        "lane_released": lane_released,
        "session_locks_released": session_locks_released,
    }


async def cancel_conversation_run(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Stop the in-process generation task, then release conversation locks.

    Explicit Stop should hard-cancel the current run. Lock release stays so the
    user can send the next message immediately. Canvas executions for the same
    conversation are stopped as well.
    """
    run_cancelled = await conversation_run_registry.request_stop(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if run_cancelled:
        handle = conversation_run_registry.get(user_id, conversation_id)
        if handle is not None:
            await handle.wait_finished()
    canvas_stopped = 0
    try:
        from app.services.ai.code_execution_service import (
            stop_executions_for_conversation,
        )

        canvas_stopped = await stop_executions_for_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        logger.warning(
            "[ConversationRunCancel] failed to stop canvas executions conversation=%s: %s",
            conversation_id,
            exc,
        )

    locks = await release_conversation_run_locks(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
    )
    success = bool(locks.get("success")) or run_cancelled or canvas_stopped > 0
    if run_cancelled or canvas_stopped:
        logger.info(
            "[ConversationRunCancel] cancelled run conversation=%s user=%s trace=%s "
            "run=%s canvas=%s",
            conversation_id,
            user_id,
            trace_id,
            run_cancelled,
            canvas_stopped,
        )
    return {
        **locks,
        "success": success,
        "run_cancelled": run_cancelled,
        "canvas_stopped": canvas_stopped,
    }
