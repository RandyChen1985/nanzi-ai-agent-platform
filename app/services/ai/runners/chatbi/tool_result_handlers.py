"""ChatBI tool result handlers — extracted from DataAgentRunner."""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.ai.runners.chatbi.constants import (
    _SQL_RESULT_DISPLAY_MAX_ROWS,
    _SQL_RESULT_ROW_KEYS,
    _SQL_TOOL_ERROR_DELIMITER,
    _SQL_TOOL_RESULT_DELIMITER,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX


def format_sql_result_for_display(runner: Any, output: Any, *, max_rows: int = _SQL_RESULT_DISPLAY_MAX_ROWS) -> str:
    parsed = runner._try_parse_json_output(output)
    if isinstance(parsed, dict) and runner._is_structured_sql_result(parsed):
        display: dict[str, Any] = dict(parsed)
        for key in _SQL_RESULT_ROW_KEYS:
            rows = display.get(key)
            if isinstance(rows, list) and len(rows) > max_rows:
                display[key] = rows[:max_rows]
                display["_display_note"] = f"仅展示前 {max_rows} 行，共 {len(rows)} 行"
                break
        try:
            return json.dumps(display, ensure_ascii=False, indent=2)
        except Exception:
            return str(output or "")
    if isinstance(parsed, list):
        if len(parsed) > max_rows:
            payload = {
                "_display_note": f"仅展示前 {max_rows} 行，共 {len(parsed)} 行",
                "rows": parsed[:max_rows],
            }
        else:
            payload = parsed
        try:
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            return str(output or "")
    return str(output or "")

def build_sql_error_tool_details(runner: Any, output: Any, tool_args: dict[str, Any] | None) -> str:
    text = str(output or "")
    marker = "[Executed SQL]:"
    if marker in text:
        error_part, sql_part = text.split(marker, 1)
        error_part = error_part.strip()
        sql_part = sql_part.strip()
    else:
        error_part = text.strip()
        sql_part = ""
        if tool_args:
            raw_sql = tool_args.get("sql") or tool_args.get("query")
            if isinstance(raw_sql, str) and raw_sql.strip():
                sql_part = raw_sql.strip()
    error_display = truncate_for_context(error_part, max_len=1000)
    if sql_part:
        return f"[Executed SQL]:\n{sql_part}\n\n{_SQL_TOOL_ERROR_DELIMITER}\n{error_display}"
    return error_display

def format_tool_details(
    runner: Any,
    tool_name: str,
    output: Any,
    state: DataRunState,
    tool_args: dict[str, Any] | None = None,
) -> str:
    if tool_name == "execute_sql_query" and not runner._is_schema_gate_block(output):
        parsed = runner._try_parse_json_output(output)
        if runner._is_structured_sql_result(parsed):
            result_text = runner._format_sql_result_for_display(output)
            result_details = truncate_for_context(result_text, max_len=1000)
            details = result_details
            output_text = str(output or "")
            if "[Executed SQL]:" not in output_text and tool_args:
                raw_sql = tool_args.get("sql") or tool_args.get("query")
                if isinstance(raw_sql, str) and raw_sql.strip():
                    details = (
                        f"[Executed SQL]:\n{raw_sql.strip()}\n\n"
                        f"{_SQL_TOOL_RESULT_DELIMITER}\n{result_details}"
                    )
        elif runner._is_failed_sql_repeat_gate_block(output):
            details = truncate_for_context(str(output or ""), max_len=1000)
        elif runner._is_sql_sandbox_gate_block(output):
            details = runner._build_sql_error_tool_details(output, tool_args)
        elif state.sql_error:
            details = runner._build_sql_error_tool_details(output, tool_args)
        else:
            result_text = runner._format_sql_result_for_display(output)
            details = truncate_for_context(result_text, max_len=1000)
            output_text = str(output or "")
            if "[Executed SQL]:" not in output_text and tool_args:
                raw_sql = tool_args.get("sql") or tool_args.get("query")
                if isinstance(raw_sql, str) and raw_sql.strip():
                    details = (
                        f"[Executed SQL]:\n{raw_sql.strip()}\n\n"
                        f"{_SQL_TOOL_RESULT_DELIMITER}\n{details}"
                    )
    else:
        details = truncate_for_context(str(output or ""), max_len=1000)
    if tool_name == "get_dataset_schema":
        from app.services.schema_chunk_format import format_schema_hit_summary

        keywords = (
            runner._schema_keywords_from_args(tool_args)
            or state.last_schema_tool_keywords
            or state.last_applied_schema_retry_keywords
            or state.last_schema_keywords
        )
        prefix_lines: list[str] = []
        if keywords:
            prefix_lines.append(f"[检索关键词] {keywords}")
        summary = format_schema_hit_summary(output)
        if summary:
            prefix_lines.append(summary)
        if prefix_lines:
            details = "\n\n".join(prefix_lines) + "\n\n" + details
    if tool_name == "execute_sql_query" and runner._is_schema_gate_block(output):
        details = f"{details}\n\n[系统检测] 已拦截：未先获取数据集定义，SQL 未执行。"
    if tool_name == "execute_sql_query" and runner._is_failed_sql_repeat_gate_block(output):
        details = f"{details}\n\n[系统检测] 已拦截：禁止原样重复执行已失败的 SQL，请修正后再试。"
    if tool_name == "execute_sql_query" and runner._is_sql_repeat_gate_block(output):
        details = f"{details}\n\n[系统检测] 已有成功非空查数结果，已拦截重复 SQL 执行。"
    if tool_name == "execute_sql_query" and runner._is_sql_static_gate_block(output):
        details = f"{details}\n\n[系统检测] SQL 存在高风险执行特征，已拦截执行。"
    if tool_name == "execute_sql_query" and runner._is_time_range_gate_block(output):
        details = f"{details}\n\n[系统检测] SQL 时间范围与相对时间锚点不一致，已拦截执行。"
    if tool_name == "execute_sql_query" and runner._is_sql_sandbox_gate_block(output):
        details = f"{details}\n\n[系统检测] SQL 存在性能安全风险（超限或笛卡尔积），已被沙箱网关拦截。"
    if tool_name == "execute_sql_query" and runner._is_sql_plan_gate_block(output):
        details = f"{details}\n\n[系统检测] 高风险 SQL 缺少结构化 SQL Plan，已拦截执行。"
    if tool_name == "execute_sql_query" and state.empty_sql_reason:
        details = f"{details}\n\n[系统检测] {state.empty_sql_reason}"
        if state.empty_filter_diagnostic_summary:
            details = f"{details}\n\n{state.empty_filter_diagnostic_summary}"
    if tool_name == "execute_sql_query" and state.duration_anomaly_reason:
        details = f"{details}\n\n[系统检测] {state.duration_anomaly_reason}"
    if tool_name == "execute_sql_query" and state.sql_error_message:
        error_message = str(state.sql_error_message or "")
        if "[Executed SQL]:" in error_message:
            error_message = error_message.split("[Executed SQL]:", 1)[0].strip()
        details = f"{details}\n\n[系统检测] SQL 执行异常: {error_message[:500]}"
    if tool_name == "get_dataset_schema" and state.schema_miss:
        details = f"{details}\n\n[系统检测] 未命中相关数据集定义。"
    if tool_name == "get_dataset_schema" and state.last_applied_schema_retry_keywords:
        details = (
            f"{details}\n\n[系统检测] 本次重试使用受控重试关键词："
            f"{state.last_applied_schema_retry_keywords}"
        )
    if tool_name == "get_dataset_schema" and state.schema_needs_refinement:
        threshold = runner._schema_similarity_threshold or 0.2
        strong = runner._schema_strong_confidence_threshold(threshold)
        details = (
            f"{details}\n\n[系统检测] Schema 检索结果相关性不足（最高置信度低于 {strong:.2f}，"
            f"检索阈值 ragflow_similarity_threshold={threshold:.2f}），将换关键词重试。"
        )
    if tool_name == "get_dataset_schema" and state.schema_ambiguous:
        details = f"{details}\n\n[系统检测] Schema 检索结果存在歧义，需要用户确认后再查数。"
    if tool_name == "get_dataset_schema" and state.no_authorized_schema:
        details = f"{details}\n\n[系统检测] 当前用户没有可用授权数据集，已终止查数流程。"
    if tool_name == "get_dataset_schema" and state.schema_service_unavailable:
        details = f"{details}\n\n[系统检测] 元数据服务不可用，已终止查数流程。"
    if tool_name == "get_dataset_schema" and state.rag_not_synced:
        details = f"{details}\n\n[系统检测] 元数据未同步到知识库，已终止查数流程。"
    if state.tool_loop_fuse_triggered and state.tool_loop_fuse_reason:
        details = f"{details}\n\n[系统检测] {state.tool_loop_fuse_reason}"
    return details

def apply_schema_tool_result(runner: Any, state: DataRunState, output: Any) -> None:
    threshold = runner._schema_similarity_threshold or 0.2
    state.schema_service_unavailable = runner._is_schema_service_unavailable(output)
    state.no_authorized_schema = runner._is_no_authorized_schema(output)
    state.rag_not_synced = runner._is_rag_not_synced(output)
    state.schema_miss = runner._is_no_relevant_schema(output)
    state.schema_needs_refinement = runner._schema_needs_refinement(output, similarity_threshold=threshold)
    state.schema_ambiguous, state.schema_ambiguous_reason = runner._detect_schema_ambiguity(output)
    weak_or_miss = state.schema_miss or state.schema_needs_refinement
    if weak_or_miss:
        state.schema_miss_count += 1
    elif not (
        state.schema_service_unavailable
        or state.no_authorized_schema
        or state.rag_not_synced
        or state.schema_ambiguous
        or not str(output or "").strip()
    ):
        state.schema_miss_count = 0
    if (
        runner._is_schema_fatal(state)
        or state.schema_miss
        or state.schema_needs_refinement
        or state.schema_ambiguous
        or not str(output or "").strip()
    ):
        state.schema_completed = False
        state.sql_before_schema = False
        state.schema_output = ""
        state.table_bindings = {}
        state.schema_table_columns = {}
        return
    state.schema_completed = True
    state.sql_before_schema = False
    from app.services.ai.chatbi_sql_query_binding import (
        bindings_to_table_columns,
        extract_schema_table_bindings,
    )

    state.schema_output = str(output or "")
    state.table_bindings = extract_schema_table_bindings(state.schema_output)
    state.schema_table_columns = bindings_to_table_columns(state.table_bindings)
    if state.schema_refresh_required:
        state.schema_refreshed_after_sql_error = True

def apply_sql_tool_result(
    runner: Any,
    state: DataRunState,
    *,
    tool_args: dict[str, Any],
    output: Any,
) -> tuple[Any, bool]:
    if runner._is_sql_repeat_gate_block(output):
        state.sql_repeat_gate_block = True
        state.sql_completed = True
        text = str(output or "")
        cached_text = text
        if "\n\n" in text:
            parts = text.split("\n\n", 1)
            cached_text = parts[1]
        state.last_successful_sql_output = cached_text
        parsed = runner._try_parse_json_output(cached_text)
        return parsed, False
    if runner._is_sql_static_gate_block(output):
        state.sql_static_risk = True
        state.sql_error = False
        state.sql_error_message = ""
        return output, False
    if runner._is_time_range_gate_block(output):
        state.time_range_anomaly = True
        if not state.time_range_anomaly_reason:
            text = str(output or "").replace(TIME_RANGE_GATE_PREFIX, "").strip()
            state.time_range_anomaly_reason = text[:800]
        state.sql_error = False
        state.sql_error_message = ""
        return output, False
    if runner._is_sql_sandbox_gate_block(output):
        state.sql_sandbox_blocked = True
        state.sql_sandbox_blocked_reason = str(output or "").replace("[Performance Blocked]", "").strip()
        state.sql_error = False
        state.sql_error_message = ""
        return output, False
    if runner._is_sql_plan_gate_block(output):
        state.sql_plan_missing = True
        state.sql_error = False
        state.sql_error_message = ""
        state.sql_completed = True
        return output, False
    if runner._is_failed_sql_repeat_gate_block(output):
        original_error = (
            runner._extract_failed_repeat_original_error(output)
            or state.last_sql_error_summary
            or state.sql_error_message
            or ""
        )
        if runner._is_failed_sql_repeat_gate_block(original_error):
            original_error = runner._extract_failed_repeat_original_error(original_error)
        original_error = str(original_error or "").strip()
        state.failed_sql_repeat_gate_block = True
        state.sql_error = True
        if original_error:
            state.sql_error_message = original_error[:1000]
            state.last_sql_error_summary = original_error[:800]
        state.sql_completed = True
        return output, False
    if runner._is_schema_gate_block(output):
        if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
            state.sql_error = True
            state.sql_error_message = str(output or "")[:1000]
            state.last_sql_error_summary = state.sql_error_message
            state.sql_completed = True
            return output, False
        state.sql_before_schema = True
        return output, False
    if not state.schema_completed:
        state.sql_before_schema = True
        return output, False

    state.sql_completed = True

    parsed_output = runner._try_parse_json_output(output)
    empty_reason = runner._detect_empty_result(parsed_output) or ""
    sql_text = ""
    if isinstance(tool_args, dict):
        sql_text = str(tool_args.get("sql") or tool_args.get("query") or "")
    is_diag = runner._is_diagnostic_sql(sql_text)

    state.sql_error, state.sql_error_message = runner._detect_sql_error(output)
    if state.sql_error and runner._is_sql_fatal_error(state.sql_error_message):
        state.sql_fatal_error = True
        state.sql_fatal_message = state.sql_error_message
    if state.sql_error:
        normalized_sql = runner._normalize_sql_text(sql_text)
        if normalized_sql:
            state.failed_sql_signatures[normalized_sql] = (
                state.failed_sql_signatures.get(normalized_sql, 0) + 1
            )
            state.last_failed_sql_normalized = normalized_sql
        state.last_sql_error_summary = str(state.sql_error_message or "")[:800]
        if (
            runner._is_schema_reference_sql_error(state.sql_error_message)
            and not runner._is_sql_schema_preflight_error(output)
        ):
            state.schema_refresh_required = True
            state.schema_refreshed_after_sql_error = False
        return parsed_output, False

    if empty_reason:
        if state.expecting_final_sql_after_diagnostic and not is_diag:
            from app.services.ai.empty_result_filter_diagnostic import (
                should_escalate_empty_after_value_correction,
            )

            if should_escalate_empty_after_value_correction(state.empty_filter_diagnostics):
                state.empty_sql_reason = "修正筛选值后仍为空，疑似 WHERE 筛选字段选错"
                state.empty_sql_result = True
                state.empty_sql_text = sql_text or state.empty_sql_text or ""
                state.expecting_final_sql_after_diagnostic = False
                state.diagnostic_sql_pending_final = False
                if state.empty_filter_diagnostic_summary:
                    state.empty_filter_diagnostic_summary = (
                        f"{state.empty_filter_diagnostic_summary}\n\n"
                        "【二次空结果】已按候选值修正后仍无数据，请优先核对 WHERE 是否使用了错误的维度字段，"
                        "必要时重新调用 get_dataset_schema 确认 term/physical_name 映射。"
                    )
                return parsed_output, False
            state.empty_sql_reason = ""
            state.empty_sql_result = False
            state.last_successful_sql_output = output
            state.expecting_final_sql_after_diagnostic = False
            state.diagnostic_sql_pending_final = False
            return parsed_output, True
        if runner._is_trusted_empty_result(sql_text, state):
            state.empty_sql_reason = ""
            state.empty_sql_result = False
            state.last_successful_sql_output = output
            state.diagnostic_sql_pending_final = False
        else:
            state.empty_sql_reason = empty_reason
            state.empty_sql_result = True
            state.empty_sql_text = sql_text or ""
        return parsed_output, False

    state.empty_sql_reason = ""
    state.empty_sql_result = False
    if is_diag and state.expecting_final_sql_after_diagnostic:
        if (
            state.diagnostic_sql_pending_final
            and runner._result_has_data_rows(parsed_output)
        ):
            state.expecting_final_sql_after_diagnostic = False
            state.diagnostic_sql_pending_final = False
        else:
            state.diagnostic_sql_pending_final = True
            return parsed_output, False

    state.expecting_final_sql_after_diagnostic = False
    state.diagnostic_sql_pending_final = False
    state.sql_static_risk = False
    state.sql_static_risk_reason = ""
    state.time_range_anomaly = False
    state.time_range_anomaly_reason = ""
    state.sql_repeat_gate_block = False
    state.failed_sql_repeat_gate_block = False
    normalized_sql = runner._normalize_sql_text(sql_text)
    if normalized_sql:
        state.failed_sql_signatures.pop(normalized_sql, None)
        if state.last_failed_sql_normalized == normalized_sql:
            state.last_failed_sql_normalized = ""
    state.schema_refresh_required = False
    state.schema_refreshed_after_sql_error = False
    state.last_sql_error_summary = ""
    state.last_successful_sql_output = output
    duration_anomaly, duration_reason = runner._detect_duration_anomaly(parsed_output)
    if duration_anomaly:
        state.duration_anomaly = True
        state.duration_anomaly_reason = duration_reason
        return parsed_output, False
    state.duration_anomaly = False
    state.duration_anomaly_reason = ""
    ratio_anomaly, anomaly_reason = runner._detect_ratio_anomaly(parsed_output)
    if ratio_anomaly:
        state.ratio_anomaly = True
        state.ratio_anomaly_reason = anomaly_reason
        return parsed_output, False
    state.empty_filter_diagnostics = []
    state.empty_filter_diagnostic_summary = ""
    return parsed_output, True

