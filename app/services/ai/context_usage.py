"""共享的会话上下文 Token 估算逻辑。"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from app.core.redis import get_redis
from app.services.ai.memory_service import memory_service
from app.services.config_service import ConfigService
from app.services.schema_chunk_format import estimate_text_tokens
from app.services.ai.runtime.agentscope.tool_result_context import is_trusted_tool_result_context

logger = logging.getLogger(__name__)


def _empty_context_breakdown() -> dict[str, Any]:
    return {
        "system_prompt_tokens": 0,
        "tools_tokens": 0,
        "conversation_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
        "source": "unavailable",
    }


def empty_context_usage() -> dict[str, Any]:
    return {
        "estimated_current_tokens": None,
        "estimated_remaining_tokens": None,
        "context_messages": None,
        "token_budget": None,
        "physical_window": None,
        "history_budget": None,
        "completion_reserve_tokens": None,
        "request_input_budget": None,
        "prompt_overhead_reservation_tokens": None,
        "overhead_reservation_tokens": None,
        "usage_percentage": None,
        "context_breakdown": None,
    }


async def _latest_runtime_context_breakdown(
    *,
    user_id: Any,
    conversation_id: str,
) -> dict[str, Any] | None:
    """读取最近一次运行上下文的固定开销，供会话总量只合并一次。"""
    if user_id is None or not conversation_id:
        return None

    try:
        from app.services.ai.runtime.agentscope.middleware import STATS_KEY_SUFFIX

        uid = str(user_id)
        key = f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:{STATS_KEY_SUFFIX}"
        redis = await get_redis()
        if not redis:
            return None
        rows = await redis.lrange(key, -1, -1)
        if not rows:
            return None
        raw = rows[-1]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        record = json.loads(raw) if isinstance(raw, str) else raw
        breakdown = record.get("context_breakdown") if isinstance(record, Mapping) else None
        if not isinstance(breakdown, Mapping):
            return None

        normalized = {
            "system_prompt_tokens": max(0, int(breakdown.get("system_prompt_tokens") or 0)),
            "tools_tokens": max(0, int(breakdown.get("tools_tokens") or 0)),
        }
        if not any(normalized.values()):
            return None
        return normalized
    except Exception as exc:
        logger.debug("读取最近运行上下文构成失败: %s", exc)
        return None


async def estimate_context_usage(
    *,
    user_id: Any,
    conversation_id: str | None,
    runtime_model_info: Mapping[str, Any] | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    empty_history_is_zero: bool = False,
) -> dict[str, Any]:
    """按 session_status 的口径估算当前会话上下文使用情况。

    ``empty_history_is_zero`` 供输入框状态接口使用：新会话也返回预算和 0 使用量；
    session_status 默认保持原有语义，无法取得历史时返回 null 占位。
    """
    empty = empty_context_usage()
    if not conversation_id:
        return empty

    try:
        if history is None:
            history = await memory_service.get_effective_context_history(user_id, conversation_id)
        if not history and not empty_history_is_zero:
            return empty

        history_tokens = int(
            sum(
                estimate_text_tokens(
                    str(message.get("content") or "")
                    + (
                        str(message.get("tool_run_text") or "")
                        if is_trusted_tool_result_context(message)
                        else ""
                    )
                )
                for message in history
                if isinstance(message, Mapping)
            )
        )

        runtime_breakdown = await _latest_runtime_context_breakdown(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if runtime_breakdown:
            context_breakdown = {
                "system_prompt_tokens": runtime_breakdown["system_prompt_tokens"],
                "tools_tokens": runtime_breakdown["tools_tokens"],
                "conversation_tokens": history_tokens,
                "total_tokens": (
                    runtime_breakdown["system_prompt_tokens"]
                    + runtime_breakdown["tools_tokens"]
                    + history_tokens
                ),
                "estimated": True,
                "source": "session_history_plus_latest_runtime_context",
            }
        else:
            context_breakdown = {
                **_empty_context_breakdown(),
                "conversation_tokens": history_tokens,
                "total_tokens": history_tokens,
                "estimated": True,
                "source": "session_history_estimate",
            }
        total_tokens = context_breakdown["total_tokens"]

        try:
            fallback_raw = await ConfigService.get("agent_context_max_tokens", "65536")
            fallback_window = int(fallback_raw)
        except (TypeError, ValueError):
            fallback_window = 65536
        if fallback_window <= 0:
            fallback_window = 65536

        info = dict(runtime_model_info or {})
        try:
            physical_window = int(info.get("physical_window") or 0)
        except (TypeError, ValueError):
            physical_window = 0
        if physical_window <= 0:
            source = info.get("source")
            if source in {"runtime_override", "debug_override", "agent_config"}:
                try:
                    physical_window = int(info.get("context_size") or 0)
                except (TypeError, ValueError):
                    physical_window = 0
        if physical_window <= 0:
            physical_window = fallback_window

        try:
            overhead = int(info.get("overhead_reservation_tokens") or 0)
        except (TypeError, ValueError):
            overhead = 0
        if overhead <= 0:
            try:
                overhead_raw = await ConfigService.get(
                    "agent_context_overhead_headroom_tokens", "8192"
                )
                overhead = max(0, int(overhead_raw))
            except (TypeError, ValueError):
                overhead = 8192

        try:
            completion_reserve = max(
                0, int(info.get("completion_reserve_tokens") or 0)
            )
        except (TypeError, ValueError):
            completion_reserve = 0
        try:
            prompt_overhead = max(
                0, int(info.get("prompt_overhead_reservation_tokens") or 0)
            )
        except (TypeError, ValueError):
            prompt_overhead = 0

        try:
            budget = int(info.get("history_budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            history_overhead = prompt_overhead or max(
                0, overhead - completion_reserve
            )
            if completion_reserve > 0:
                budget = max(
                    1, physical_window - completion_reserve - history_overhead
                )
            else:
                budget = max(physical_window - overhead, max(1, physical_window // 3))

        try:
            request_input_budget = int(info.get("request_input_budget") or 0)
        except (TypeError, ValueError):
            request_input_budget = 0
        if request_input_budget <= 0:
            request_input_budget = max(1, physical_window - completion_reserve)

        remaining = max(0, budget - total_tokens)
        percentage = round(total_tokens / budget * 100, 1) if budget > 0 else 0.0
        return {
            "estimated_current_tokens": total_tokens,
            "estimated_remaining_tokens": remaining,
            "context_messages": len(history),
            "token_budget": budget,
            "physical_window": physical_window,
            "history_budget": budget,
            "completion_reserve_tokens": completion_reserve,
            "request_input_budget": request_input_budget,
            "prompt_overhead_reservation_tokens": prompt_overhead,
            "overhead_reservation_tokens": overhead,
            "usage_percentage": percentage,
            "context_breakdown": context_breakdown,
        }
    except Exception:
        return empty
