"""ChatBI WHERE 条件样例探查：SQL 因 WHERE 类型/格式错误失败后自动读源表样例。"""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.where_condition_sample_diagnostic import (
    AutoWhereFormatRetryResult,
    is_where_condition_sql_error,
    try_automatic_where_condition_repair,
    schema_column_hints_from_bindings,
)

logger = logging.getLogger(__name__)


def _where_probe_schema_context(state: DataRunState, *, error_message: str) -> dict[str, Any]:
    return {
        "schema_table_columns": state.schema_table_columns or None,
        "schema_column_hints": schema_column_hints_from_bindings(state.table_bindings) or None,
        "error_message": error_message,
    }


async def maybe_run_where_condition_diagnostics(
    runner: Any,
    state: DataRunState,
    *,
    tool_args: dict[str, Any],
) -> AutoWhereFormatRetryResult | None:
    """
    WHERE 相关 SQL 执行失败后：
    1) 自动探查源表样例
    2) 若能推断出格式修正，自动改写 WHERE 并重试 1 次业务 SQL
    """
    if not state.sql_error:
        return None
    from app.services.ai.runners.chatbi.platform_auto_retry import platform_auto_retry_budget_exhausted

    if platform_auto_retry_budget_exhausted(state):
        logger.info(
            "[DataAgentRunner] WHERE condition diagnostics skipped: platform auto-retry budget exhausted (%s)",
            state.platform_auto_sql_attempts,
        )
        return None
    error_message = str(state.last_sql_error_summary or state.sql_error_message or "")
    if not is_where_condition_sql_error(error_message):
        return None

    sql_text = (
        state.last_failed_sql_text
        or str(tool_args.get("sql") or tool_args.get("query") or "")
    ).strip()
    if not sql_text:
        return None

    data_source = str(tool_args.get("data_source") or "").strip()
    dataset_name = str(tool_args.get("dataset_name") or "").strip()
    if not data_source or not dataset_name:
        return None

    from app.core.orm import AsyncSessionLocal
    from app.services.sql_query_execution_service import execute_sql_query_core

    async def _execute_sql(**kwargs: Any) -> str:
        async with AsyncSessionLocal() as session:
            return await execute_sql_query_core(session, dry_run=False, **kwargs)

    schema_context = _where_probe_schema_context(state, error_message=error_message)

    try:
        retry = await try_automatic_where_condition_repair(
            sql=sql_text,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=runner._current_user_id(),
            is_admin=runner._current_user_is_admin(),
            execute_sql=_execute_sql,
            error_message=error_message,
            **schema_context,
        )
    except Exception as exc:
        logger.warning("[DataAgentRunner] WHERE condition diagnostics skipped: %s", exc)
        return None

    if not (retry.probe_summary or retry.attempted or retry.corrected_sql):
        return None

    if retry.probe_summary:
        state.where_condition_diagnostic_summary = retry.probe_summary
        state.where_condition_diagnostics = []
    if retry.summary:
        summary = state.where_condition_diagnostic_summary
        state.where_condition_diagnostic_summary = (
            f"{summary}\n\n{retry.summary}".strip() if summary else retry.summary
        )
    if retry.corrected_sql:
        state.where_condition_auto_retry_sql = retry.corrected_sql
    return retry if (retry.attempted or retry.probe_summary) else None
