"""ChatBI tool gate wrapper — wraps schema/SQL tools with platform gates."""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any

from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runners.chatbi.constants import (
    FAILED_SQL_REPEAT_GATE_PREFIX,
    SCHEMA_GATE_PREFIX,
    SQL_REPEAT_GATE_PREFIX,
    SQL_STATIC_GATE_PREFIX,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.time_anchor import build_time_range_gate_message, detect_time_range_mismatch


def wrap_tools_with_schema_gate(runner: Any, tools: list[RuntimeToolSpec], state: DataRunState) -> list[RuntimeToolSpec]:
    if not state.requires_fresh_data:
        return tools
    wrapped: list[RuntimeToolSpec] = []
    for spec in tools:
        if spec.name == "get_dataset_schema":
            original_callable = spec.callable

            async def invoke_schema_controlled(*, _original=original_callable, **kwargs: Any) -> Any:
                controlled_keywords = str(state.controlled_schema_retry_keywords or "").strip()
                use_controlled = bool(
                    controlled_keywords and (state.pending_schema_retry or state.schema_miss)
                )
                if use_controlled:
                    kwargs["keywords"] = controlled_keywords
                    state.last_applied_schema_retry_keywords = controlled_keywords
                    state.pending_schema_retry = False
                else:
                    state.last_applied_schema_retry_keywords = ""
                applied_kw = kwargs.get("keywords") or kwargs.get("query")
                state.last_schema_tool_keywords = str(applied_kw or "").strip()
                result = _original(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result

            wrapped.append(replace(spec, callable=invoke_schema_controlled))
            continue
        if spec.name != "execute_sql_query":
            wrapped.append(spec)
            continue
        original_callable = spec.callable

        async def invoke_sql_gated(*, _original=original_callable, **kwargs: Any) -> Any:
            if state.schema_ambiguous:
                return (
                    f"{SCHEMA_GATE_PREFIX} 当前 Schema 检索返回多个高置信度候选，"
                    "需要先请用户确认具体数据集或指标口径，禁止直接执行 SQL。"
                )
            if not state.schema_completed:
                return (
                    f"{SCHEMA_GATE_PREFIX} 为保证数据准确性，必须先调用 get_dataset_schema "
                    "获取数据集定义，再执行 execute_sql_query。"
                )
            if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
                return (
                    f"{SCHEMA_GATE_PREFIX} 上一轮 SQL 因字段/表引用错误失败，必须先重新调用 "
                    "get_dataset_schema 核对物理列名、表名与 JOIN 键，再修正并执行 SQL。"
                )
            current_sql = str(kwargs.get("sql") or kwargs.get("query") or "").strip()
            current_sql_normalized = runner._normalize_sql_text(current_sql)
            should_generate_sql_plan = state.requires_sql_plan and (
                not state.sql_plan_seen
                or (
                    state.sql_plan_auto_generated
                    and state.sql_plan_sql_normalized != current_sql_normalized
                )
            )
            if should_generate_sql_plan:
                from app.services.ai.runners.chatbi.sql_plan import build_platform_sql_plan

                state.sql_plan_payload = build_platform_sql_plan(
                    runner,
                    state,
                    sql=current_sql,
                    data_source=str(kwargs.get("data_source") or ""),
                    dataset_name=str(kwargs.get("dataset_name") or ""),
                )
                state.sql_plan_seen = True
                state.sql_plan_missing = False
                state.sql_plan_auto_generated = True
                state.sql_plan_sql_normalized = current_sql_normalized
            if current_sql_normalized:
                prior_failures = state.failed_sql_signatures.get(current_sql_normalized, 0)
                if prior_failures >= 1:
                    summary = str(state.last_sql_error_summary or "").strip()
                    summary_line = f"\n上次错误摘要：{summary[:400]}" if summary else ""
                    return (
                        f"{FAILED_SQL_REPEAT_GATE_PREFIX} 该 SQL 已在上一轮执行失败，"
                        "禁止原样重复提交。请根据错误信息修正字段名、表名、JOIN 条件、"
                        f"筛选条件或聚合逻辑后再调用 execute_sql_query。{summary_line}"
                    )
            from app.services.ai.chatbi_sql_query_binding import (
                build_sql_query_binding,
                reset_current_sql_query_binding,
                set_current_sql_query_binding,
            )
            from app.services.sql_query_execution_service import dialect_from_data_source

            dialect = dialect_from_data_source(str(kwargs.get("data_source") or ""))
            state.sql_query_binding = build_sql_query_binding(
                schema_output=state.schema_output,
                sql=current_sql,
                primary_dataset_name=str(kwargs.get("dataset_name") or ""),
                table_bindings=state.table_bindings,
                dialect=dialect,
            )
            preflight_error = await runner._resolve_sql_schema_preflight_error(
                current_sql,
                str(kwargs.get("data_source") or ""),
                binding=state.sql_query_binding,
                schema_table_columns=state.schema_table_columns,
                allowed_dataset_names={
                    str(item.get("dataset_name") or item.get("name") or item.get("id") or "").strip()
                    for item in (runner.debug_options.get("resource_scope", {}).get("datasets", []) or [])
                    if isinstance(item, dict) and str(item.get("id") or item.get("name") or "").strip()
                } or None,
            )
            if preflight_error:
                return preflight_error
            time_range_risk = detect_time_range_mismatch(runner._standalone_query or "", current_sql)
            if time_range_risk:
                state.time_range_anomaly = True
                state.time_range_anomaly_reason = time_range_risk
                return build_time_range_gate_message(time_range_risk)
            static_risk = ""
            if not (state.requires_sql_plan and not state.sql_plan_seen):
                static_risk = runner._detect_sql_static_risk(current_sql)
            if static_risk:
                state.sql_static_risk = True
                state.sql_static_risk_reason = static_risk
                return (
                    f"{SQL_STATIC_GATE_PREFIX} SQL 存在高风险执行特征，已阻止执行：{static_risk}\n"
                    "请收窄时间范围、补充 LIMIT，或修正 JOIN 条件后重新调用 execute_sql_query。"
                )
            if current_sql_normalized and current_sql_normalized in state.successful_sqls:
                cached_output = state.successful_sqls[current_sql_normalized]
                return (
                    f"{SQL_REPEAT_GATE_PREFIX} 本轮已成功执行过相同的 SQL 查询，禁止重复 execute_sql_query。\n"
                    "为保证正常输出，系统已自动为您加载该 SQL 上一次查询成功的缓存数据结果，"
                    "请直接基于此数据进行回答，无需再次调用查数工具：\n\n"
                    f"{cached_output}"
                )
            binding_token = set_current_sql_query_binding(state.sql_query_binding)
            try:
                result = _original(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                try:
                    if (
                        result
                        and not runner._is_schema_gate_block(result)
                        and not runner._is_sql_repeat_gate_block(result)
                        and not runner._is_sql_static_gate_block(result)
                        and not runner._is_time_range_gate_block(result)
                        and not runner._is_sql_plan_gate_block(result)
                        and not runner._is_sql_sandbox_gate_block(result)
                    ):
                        parsed_output = runner._try_parse_json_output(result)
                        empty_reason = runner._detect_empty_result(parsed_output)
                        sql_error, _ = runner._detect_sql_error(result)
                        duration_anomaly, _ = runner._detect_duration_anomaly(parsed_output)
                        if not sql_error and not empty_reason and not duration_anomaly:
                            if current_sql_normalized:
                                state.successful_sqls[current_sql_normalized] = result
                            state.last_successful_sql_output = result
                except Exception:
                    pass
                return result
            finally:
                reset_current_sql_query_binding(binding_token)

        wrapped.append(replace(spec, callable=invoke_sql_gated))
    return wrapped
