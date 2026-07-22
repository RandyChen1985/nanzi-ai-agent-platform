"""Shared pre-execute SQL gates for federated subqueries (aligned with single-source ChatBI)."""

from __future__ import annotations

from typing import Any

from app.services.ai.runners.chatbi.constants import SQL_STATIC_GATE_PREFIX
from app.services.ai.runners.chatbi.sql_gates import detect_sql_static_risk
from app.services.ai.time_anchor import build_time_range_gate_message, detect_time_range_mismatch
from app.services.sql_query_execution_service import dialect_from_data_source
from app.services.conversation_resource_service import ConversationResourceService

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
    debug_options = getattr(agent_runner, "debug_options", None)
    resource_scope = (
        (debug_options.get("resource_scope") or {})
        if isinstance(debug_options, dict)
        else {}
    )
    allowed_dataset_names = ConversationResourceService.dataset_names(resource_scope)
    if ConversationResourceService.has_dataset_scope(resource_scope) and not allowed_dataset_names:
        return "会话数据集范围缺少有效数据集名，已阻止联邦子查询。"
    if allowed_dataset_names and dataset_name not in allowed_dataset_names:
        return (
            f"当前项目会话未挂载数据集「{dataset_name}」，"
            "禁止执行该联邦子查询。请先在项目会话资源范围中追加该数据集。"
        )

    from app.services.ai.chatbi_sql_query_binding import (
        build_sql_query_binding,
        resolve_sql_schema_preflight_with_binding,
    )

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

    user_info = getattr(agent_runner, "user_info", None) or {}
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    try:
        user_id = int(raw_user_id) if raw_user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    preflight_error = await resolve_sql_schema_preflight_with_binding(
        session,
        sql=sql_text,
        binding=binding,
        schema_table_columns=schema_table_columns,
        data_source=data_source,
        user_id=user_id,
        is_admin=user_info.get("role") == "admin" or user_info.get("is_admin") is True,
        allowed_dataset_names=allowed_dataset_names or None,
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
