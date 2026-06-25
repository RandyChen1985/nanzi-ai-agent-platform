"""ChatBI empty SQL result filter diagnostics and auto-retry."""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState

logger = logging.getLogger(__name__)


def should_replace_generic_empty_failure_reply(state: DataRunState) -> bool:
    from app.services.ai.empty_result_filter_diagnostic import looks_like_generic_sql_failure_reply

    if not looks_like_generic_sql_failure_reply(state.full_content):
        return False
    return bool(state.empty_sql_result or state.empty_filter_diagnostics or state.empty_sql_text)


async def maybe_run_empty_filter_diagnostics(
    runner: Any,
    state: DataRunState,
    *,
    tool_args: dict[str, Any],
):
    if not state.empty_sql_result:
        return None
    from app.services.ai.runners.chatbi.platform_auto_retry import platform_auto_retry_budget_exhausted

    if platform_auto_retry_budget_exhausted(state):
        logger.info(
            "[DataAgentRunner] Empty filter diagnostics skipped: platform auto-retry budget exhausted (%s)",
            state.platform_auto_sql_attempts,
        )
        return None
    sql_text = state.empty_sql_text or str(tool_args.get("sql") or tool_args.get("query") or "")
    from app.services.ai.empty_result_filter_diagnostic import (
        format_repair_diagnostic_block,
        run_automatic_filter_retry,
        run_empty_filter_diagnostics,
        sql_has_string_literal_filters,
    )

    if not sql_has_string_literal_filters(sql_text):
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

    try:
        diagnostics = await run_empty_filter_diagnostics(
            sql=sql_text,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=runner._current_user_id(),
            is_admin=runner._current_user_is_admin(),
            execute_sql=_execute_sql,
            schema_table_columns=state.schema_table_columns,
        )
    except Exception as exc:
        logger.warning("[DataAgentRunner] Empty filter diagnostics skipped: %s", exc)
        return None
    state.empty_filter_diagnostics = [item.to_dict() for item in diagnostics]
    state.empty_filter_diagnostic_summary = format_repair_diagnostic_block(diagnostics)
    try:
        retry = await run_automatic_filter_retry(
            sql=sql_text,
            diagnostics=diagnostics,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=runner._current_user_id(),
            is_admin=runner._current_user_is_admin(),
            execute_sql=_execute_sql,
        )
    except Exception as exc:
        logger.warning("[DataAgentRunner] Empty filter auto retry skipped: %s", exc)
        return None
    if retry.attempted and retry.corrected_sql:
        state.empty_filter_auto_retry_sql = retry.corrected_sql
    if retry.attempted and retry.summary:
        summary = state.empty_filter_diagnostic_summary
        state.empty_filter_diagnostic_summary = (
            f"{summary}\n\n{retry.summary}".strip() if summary else retry.summary
        )
    return retry if retry.attempted else None


def apply_auto_retry_sql_result(
    runner: Any,
    state: DataRunState,
    *,
    sql_text: str,
    output: Any,
    parsed_output: Any,
) -> bool:
    state.empty_sql_result = False
    state.empty_sql_reason = ""
    state.empty_sql_text = ""
    state.expecting_final_sql_after_diagnostic = False
    state.diagnostic_sql_pending_final = False
    state.sql_completed = True
    state.last_successful_sql_output = output
    state.duration_anomaly = False
    state.duration_anomaly_reason = ""
    state.ratio_anomaly = False
    state.ratio_anomaly_reason = ""
    duration_anomaly, duration_reason = runner._detect_duration_anomaly(parsed_output)
    if duration_anomaly:
        state.duration_anomaly = True
        state.duration_anomaly_reason = duration_reason
        return False
    ratio_anomaly, anomaly_reason = runner._detect_ratio_anomaly(parsed_output)
    if ratio_anomaly:
        state.ratio_anomaly = True
        state.ratio_anomaly_reason = anomaly_reason
        return False
    normalized_sql = runner._normalize_sql_text(sql_text)
    if normalized_sql:
        state.failed_sql_signatures.pop(normalized_sql, None)
    return True
