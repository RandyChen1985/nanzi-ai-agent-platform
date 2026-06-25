"""Shared pre-execute SQL gates for federated subqueries (aligned with single-source ChatBI)."""

from __future__ import annotations

from typing import Any

from app.services.ai.runners.chatbi.constants import SQL_STATIC_GATE_PREFIX
from app.services.ai.runners.chatbi.sql_gates import detect_sql_static_risk
from app.services.ai.time_anchor import build_time_range_gate_message, detect_time_range_mismatch
from app.services.sql_query_execution_service import dialect_from_data_source

FAILED_FEDERATED_SQL_REPEAT_PREFIX = "[FAILED_SQL_REPEAT_GATE]"


async def validate_federated_subquery_before_execute(
    agent_runner: Any,
    *,
    session: Any,
    sub_sql: str,
    dataset: Any,
    schema_output: str,
    sql_query_binding: Any | None,
    user_question: str,
) -> str | None:
    """Return a blocking error message, or None if execution may proceed."""
    sql_text = str(sub_sql or "").strip()
    if not sql_text:
        return "子查询 SQL 为空，无法执行。"

    data_source = str(getattr(dataset, "data_source", "") or "")
    dataset_name = str(getattr(dataset, "name", "") or "")

    from app.services.ai.chatbi_sql_query_binding import build_sql_query_binding

    dialect = dialect_from_data_source(data_source)
    binding = sql_query_binding
    if binding is None:
        binding = build_sql_query_binding(
            schema_output=schema_output,
            sql=sql_text,
            primary_dataset_name=dataset_name,
            dialect=dialect,
        )
    schema_table_columns = None
    if binding is not None:
        cols = binding.schema_table_columns()
        schema_table_columns = cols if cols else None

    preflight_error = await agent_runner._resolve_sql_schema_preflight_error(
        sql_text,
        data_source,
        binding=binding,
        schema_table_columns=schema_table_columns,
    )
    if preflight_error:
        return preflight_error

    time_range_risk = detect_time_range_mismatch(user_question, sql_text)
    if time_range_risk:
        return build_time_range_gate_message(time_range_risk)

    static_risk = detect_sql_static_risk(sql_text)
    if static_risk:
        return (
            f"{SQL_STATIC_GATE_PREFIX} SQL 存在高风险执行特征，已阻止执行：{static_risk}\n"
            "请收窄时间范围、补充 LIMIT，或修正 JOIN 条件后重新生成子查询。"
        )
    return None


def federated_failed_sql_repeat_message(*, summary: str = "") -> str:
    summary_line = f"\n上次错误摘要：{summary[:400]}" if summary else ""
    return (
        f"{FAILED_FEDERATED_SQL_REPEAT_PREFIX} 该 SQL 已在上一轮联邦子查询中执行失败，"
        "禁止原样重复提交。请修正字段名、表名、JOIN 条件、筛选条件或聚合逻辑后再试。"
        f"{summary_line}"
    )
