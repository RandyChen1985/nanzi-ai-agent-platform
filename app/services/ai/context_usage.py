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

# 历史消息之外必须预留的物理窗口 Token（系统提示、工具 schema、技能提示、路由注入
# 内容、本轮用户消息）。
#
# 为什么是常量而不是系统配置项：这个值取决于「当前 Agent 绑定了多少工具、系统提示多长」，
# 管理员在自己的环境里**无法凭经验估出正确值**，把它做成配置只会制造一个"填错反而更糟"
# 的旋钮（填小了不压缩、填大了过度压缩，两种都难归因）。因此这里固定一个保守默认值，
# 由开发者按实际场景调整。
#
# 何时该调整：绑定了大量 MCP 工具、或系统提示显著变长时，真实开销会明显超过 8192，
# 此时历史预算会被算得过宽、最终请求可能超出模型窗口。症状是
# `ModelCallStatsMiddleware` 打出 "Input exceeds model context window" 告警。
# 真的需要按环境覆盖时，走 `runtime_model_info["prompt_overhead_reservation_tokens"]`
# （由 `_runtime_context_metadata` 产出），而不是新增一个全局配置项。
CONTEXT_OVERHEAD_RESERVATION_TOKENS = 8192

# 展示值实测校准（P2）的护栏。样本对话部分太小、或「实测 ÷ 估算」的比值过于异常时
# 宁可不校准，也不要把一个异常样本放大到用户看到的水位上。
MIN_CALIBRATION_SAMPLE_TOKENS = 1000
MIN_CALIBRATION_RATIO = 0.5
MAX_CALIBRATION_RATIO = 5.0


def _empty_context_breakdown() -> dict[str, Any]:
    return {
        "system_prompt_tokens": 0,
        "tools_tokens": 0,
        "conversation_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
        "source": "unavailable",
    }


def _empty_calibration() -> dict[str, Any]:
    """未校准（无样本或未开启）时的固定形状，前端只需判断 applied。"""
    return {
        "applied": False,
        "ratio": None,
        "sample_estimated_tokens": None,
        "sample_actual_tokens": None,
    }


def empty_context_usage() -> dict[str, Any]:
    return {
        "estimated_current_tokens": None,
        "current_tokens": None,
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
        "calibration": _empty_calibration(),
    }


async def _latest_runtime_context_sample(
    *,
    user_id: Any,
    conversation_id: str,
) -> dict[str, Any] | None:
    """读取最近一次模型调用的「运行时构成 + 该次真实用量」。

    一条调用记录里同时含当次的 `context_breakdown.total_tokens`（估算）与 `input_tokens`
    （模型返回的真实 usage），因此展示值校准所需的「实测 ÷ 估算」比值**无需新增采集**。
    前两个字段用于把会话总量里的固定开销只合并一次；后两个字段仅用于校准。
    """
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
            "estimated_total_tokens": max(0, int(breakdown.get("total_tokens") or 0)),
            "actual_input_tokens": max(0, int(record.get("input_tokens") or 0)),
        }
        if not any(
            (normalized["system_prompt_tokens"], normalized["tools_tokens"])
        ):
            return None
        return normalized
    except Exception as exc:
        logger.debug("读取最近运行上下文构成失败: %s", exc)
        return None


def _conversation_calibration_ratio(
    sample: Mapping[str, Any] | None,
    *,
    system_prompt_tokens: int,
    tools_tokens: int,
) -> float | None:
    """按最近一次调用的「实测 ÷ 估算」得到对话部分的校准系数。

    只校准对话消息：system 提示词与工具 schema 两项本来就是 AgentScope `count_tokens`
    的真实计数，把它们一起缩放会重复放大误差。样本异常（缺实测、对话部分太小、
    比值越界）时返回 None，调用方退回纯估算。
    """
    if not sample:
        return None
    try:
        estimated_total = int(sample.get("estimated_total_tokens") or 0)
        actual_total = int(sample.get("actual_input_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if estimated_total <= 0 or actual_total <= 0:
        return None
    fixed_overhead = max(0, int(system_prompt_tokens)) + max(0, int(tools_tokens))
    estimated_conversation = max(0, estimated_total - fixed_overhead)
    actual_conversation = max(0, actual_total - fixed_overhead)
    if (
        estimated_conversation < MIN_CALIBRATION_SAMPLE_TOKENS
        or actual_conversation <= 0
    ):
        return None
    ratio = actual_conversation / estimated_conversation
    if not (MIN_CALIBRATION_RATIO <= ratio <= MAX_CALIBRATION_RATIO):
        return None
    return ratio


async def estimate_context_usage(
    *,
    user_id: Any,
    conversation_id: str | None,
    runtime_model_info: Mapping[str, Any] | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    empty_history_is_zero: bool = False,
    calibrate: bool = False,
) -> dict[str, Any]:
    """按 session_status 的口径估算当前会话上下文使用情况。

    ``empty_history_is_zero`` 供输入框状态接口使用：新会话也返回预算和 0 使用量；
    session_status 默认保持原有语义，无法取得历史时返回 null 占位。

    ``calibrate`` **只给展示层用**（输入框浮标端点）：用最近一次模型调用的真实 usage
    校准对话部分的估算，结果放在 ``current_tokens``，纯估算仍保留在
    ``estimated_current_tokens``。默认 False，因此 session_status 工具与运行时裁剪
    判定看到的水位不变——`estimate_text_tokens` 的注释要求裁剪水位线、占用展示与
    AgentScope 内部压缩使用同一度量，单独改判定会与它打架。
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

        runtime_sample = await _latest_runtime_context_sample(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        sample_system_tokens = int(runtime_sample["system_prompt_tokens"]) if runtime_sample else 0
        sample_tools_tokens = int(runtime_sample["tools_tokens"]) if runtime_sample else 0
        # 纯估算：始终是「当前历史 + 最近一次运行的固定开销」，不受校准影响
        estimated_total_tokens = sample_system_tokens + sample_tools_tokens + history_tokens

        ratio = (
            _conversation_calibration_ratio(
                runtime_sample,
                system_prompt_tokens=sample_system_tokens,
                tools_tokens=sample_tools_tokens,
            )
            if calibrate
            else None
        )
        if ratio is not None and runtime_sample is not None:
            conversation_tokens = int(round(history_tokens * ratio))
            calibration = {
                "applied": True,
                "ratio": ratio,
                "sample_estimated_tokens": int(
                    runtime_sample.get("estimated_total_tokens") or 0
                ),
                "sample_actual_tokens": int(
                    runtime_sample.get("actual_input_tokens") or 0
                ),
            }
        else:
            conversation_tokens = history_tokens
            calibration = _empty_calibration()

        if runtime_sample:
            context_breakdown = {
                "system_prompt_tokens": sample_system_tokens,
                "tools_tokens": sample_tools_tokens,
                "conversation_tokens": conversation_tokens,
                "total_tokens": (
                    sample_system_tokens + sample_tools_tokens + conversation_tokens
                ),
                "estimated": True,
                "source": "session_history_plus_latest_runtime_context",
            }
        else:
            context_breakdown = {
                **_empty_context_breakdown(),
                "conversation_tokens": conversation_tokens,
                "total_tokens": conversation_tokens,
                "estimated": True,
                "source": "session_history_estimate",
            }
        # 下游（剩余量 / 百分比）一律用展示值，保证面板自洽
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
            if source in {"runtime_override", "request_override", "agent_config"}:
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
            overhead = CONTEXT_OVERHEAD_RESERVATION_TOKENS

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
            "estimated_current_tokens": estimated_total_tokens,
            "current_tokens": total_tokens,
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
            "calibration": calibration,
        }
    except Exception:
        return empty
