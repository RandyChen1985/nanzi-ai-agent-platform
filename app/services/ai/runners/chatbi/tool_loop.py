"""Tool call loop detection and signature tracking."""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.runtime.tool_loop_detector import (
    LOOP_DETECTION_EXEMPT_TOOL_NAMES,
    ToolLoopDetector,
)
from app.services.ai.runners.chatbi.constants import (
    FAILED_SQL_REPEAT_THRESHOLD,
    TOOL_LOOP_FUSE_THRESHOLD,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_fatal import is_schema_fatal


def normalize_tool_arg_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize_tool_arg_value(value[key])
            for key in sorted(value.keys(), key=str)
        }
    if isinstance(value, list):
        return [normalize_tool_arg_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.strip().split())
    return value


def tool_call_signature(tool_name: str, tool_args: dict[str, Any] | None) -> str:
    normalized_args = normalize_tool_arg_value(tool_args or {})
    try:
        args_text = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        args_text = str(normalized_args)
    return f"{tool_name}:{args_text}"


def tool_call_made_progress(state: DataRunState, tool_name: str) -> bool:
    if tool_name == "get_dataset_schema":
        return (
            state.schema_completed
            and not is_schema_fatal(state)
            and not state.schema_miss
            and not state.schema_needs_refinement
            and not state.schema_ambiguous
        )
    if tool_name == "execute_sql_query":
        return (
            state.sql_completed
            and not state.sql_error
            and not state.empty_sql_result
            and not state.sql_plan_missing
            and not state.sql_static_risk
            and not state.time_range_anomaly
            and not state.sql_repeat_gate_block
            and not state.failed_sql_repeat_gate_block
            and not state.duration_anomaly
            and not state.diagnostic_sql_pending_final
        )
    return False


def record_tool_call_signature(
    state: DataRunState,
    tool_name: str,
    tool_args: dict[str, Any] | None,
) -> None:
    if not tool_name or state.tool_loop_fuse_triggered:
        return
    # 与通用链路同一份白名单：记账类工具不占额度、不判熔断。
    # 单独早退是为了连 `state.tool_call_signatures` 也不记——那张表是 ChatBI
    # 自己的计数，只豁免 detector 会留下一半记账、一半不记的矛盾状态。
    if tool_name in LOOP_DETECTION_EXEMPT_TOOL_NAMES:
        return
    signature = tool_call_signature(tool_name, tool_args)
    if tool_call_made_progress(state, tool_name):
        state.tool_call_signatures.pop(signature, None)
        state.tool_loop_detector = ToolLoopDetector()
        return
    fuse_threshold = TOOL_LOOP_FUSE_THRESHOLD
    if tool_name == "execute_sql_query" and (
        state.last_sql_error_summary or state.failed_sql_signatures
    ):
        fuse_threshold = FAILED_SQL_REPEAT_THRESHOLD
    verdict = state.tool_loop_detector.record(tool_name, tool_args)
    count = verdict.count
    if verdict.fused:
        state.tool_loop_fuse_triggered = True
        state.halt_current_react = True
        state.tool_loop_fuse_reason = verdict.message
        return
    if count >= fuse_threshold:
        state.tool_loop_fuse_triggered = True
        state.halt_current_react = True
        suffix = (
            "，且近期存在 SQL 执行失败，系统判断继续原样重试只会消耗步数。"
            if fuse_threshold < TOOL_LOOP_FUSE_THRESHOLD
            else "，系统判断继续执行大概率只会消耗步数。"
        )
        state.tool_loop_fuse_reason = (
            f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次{suffix}"
        )
        return
    count = state.tool_call_signatures.get(signature, 0) + 1
    state.tool_call_signatures[signature] = count
    if count >= fuse_threshold:
        state.tool_loop_fuse_triggered = True
        state.halt_current_react = True
        suffix = (
            "，且近期存在 SQL 执行失败，系统判断继续原样重试只会消耗步数。"
            if fuse_threshold < TOOL_LOOP_FUSE_THRESHOLD
            else "，系统判断继续执行大概率只会消耗步数。"
        )
        state.tool_loop_fuse_reason = (
            f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次{suffix}"
        )
