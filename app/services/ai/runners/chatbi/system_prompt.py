"""ChatBI system prompt assembly."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import build_data_query_time_anchor_block


def build_data_query_state_hint(
    runner: Any,
    *,
    context_action_result: Optional[Dict[str, Any]] = None,
    include_context_action: bool = False,
) -> str:
    """Build a read-only state summary without changing ChatBI gates or tool choice."""
    requires_fresh_data = bool(getattr(runner, "_requires_fresh_data", True))
    requires_sql_query = bool(getattr(runner, "_requires_sql_query", True))
    reusable_result = bool(context_action_result) and not requires_fresh_data
    route_hints = getattr(runner, "route_hints", {}) or {}
    evidence_metadata = getattr(runner, "_evidence_metadata", {}) or {}
    if not evidence_metadata and isinstance(context_action_result, dict):
        evidence_metadata = {
            "status": context_action_result.get("result_status") or "unknown",
            "source_ref": context_action_result.get("source_ref"),
            "observed_at": context_action_result.get("observed_at"),
            "source_as_of": context_action_result.get("data_as_of")
            or context_action_result.get("source_as_of"),
            "freshness": context_action_result.get("freshness") or "unknown",
        }

    if reusable_result or include_context_action:
        allowed_next_action = "reuse_previous_result"
    elif requires_fresh_data and requires_sql_query:
        allowed_next_action = "get_dataset_schema"
    elif requires_fresh_data:
        allowed_next_action = "get_dataset_schema_or_clarify"
    else:
        allowed_next_action = "answer_from_context"

    lines = [
        "[DATA_QUERY_STATE]",
        f"fresh_data_required: {str(requires_fresh_data).lower()}",
        f"reusable_result: {str(reusable_result).lower()}",
        f"sql_query_required: {str(requires_sql_query).lower()}",
        f"allowed_next_action: {allowed_next_action}",
    ]
    for key in (
        "semantic_domain",
        "semantic_operation",
        "fact_kind",
        "freshness_requirement",
        "time_scope",
        "reference_mode",
        "needs_fresh_data",
    ):
        value = route_hints.get(key)
        if value not in (None, "", "unknown"):
            lines.append(f"{key}: {str(value).lower()}")
    if requires_fresh_data:
        lines.append("schema_ready: false")
    lines.append("[/DATA_QUERY_STATE]")
    if evidence_metadata:
        lines.extend(
            [
                "[EVIDENCE_STATE]",
                f"result_status: {evidence_metadata.get('status') or 'unknown'}",
                f"source_ref: {evidence_metadata.get('source_ref') or 'unknown'}",
                f"observed_at: {evidence_metadata.get('observed_at') or 'unknown'}",
                f"source_as_of: {evidence_metadata.get('source_as_of') or 'unknown'}",
                f"freshness: {evidence_metadata.get('freshness') or 'unknown'}",
                "[/EVIDENCE_STATE]",
            ]
        )
    return "\n".join(lines)


async def build_system_content(
    runner: Any,
    *,
    context_action_result: Optional[Dict[str, Any]] = None,
    include_context_action: bool = False,
) -> str:
    system_prompt = runner.config.system_prompt or ""
    if "{dataset_menu}" in system_prompt:
        user_id = runner.user_info.get("user_id") if runner.user_info else None
        is_admin = runner.user_info.get("role") == "admin" if runner.user_info else False
        dataset_menu = await AgentConfigProvider.get_dataset_menu(
            user_id=user_id,
            is_admin=is_admin,
        )
        system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
    context_action_prompt = ""
    if include_context_action:
        result_json = ""
        if context_action_result:
            result_json = json.dumps(context_action_result, ensure_ascii=False)
            if len(result_json) > 20000:
                result_json = result_json[:20000] + "\n... [上一轮结果过长已截断]"
        context_action_prompt = f"\n\n{DataQueryPrompts.context_action_guide(result_json)}"
    time_anchor = build_data_query_time_anchor_block()
    sql_plan_block = (
        DataQueryPrompts.SQL_PLAN_ENFORCEMENT + "\n\n"
        if runner._is_sql_plan_enabled()
        else ""
    )
    state_hint = build_data_query_state_hint(
        runner,
        context_action_result=context_action_result,
        include_context_action=include_context_action,
    )
    return (
        f"{DataQueryPrompts.GLOBAL_GUARDRAILS}\n\n"
        f"{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}\n\n"
        f"{sql_plan_block}"
        f"{time_anchor}\n\n"
        f"{DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT}\n\n"
        f"{state_hint}\n\n"
        f"{system_prompt}{context_action_prompt}"
    )
