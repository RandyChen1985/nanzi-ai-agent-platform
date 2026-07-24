"""ChatBI follow-up data result persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _result_status(value: Any) -> str:
    from app.services.ai.grounding.ledger import classify_evidence_result

    return classify_evidence_result(value).value


async def load_last_data_result(runner: Any) -> Optional[Dict[str, Any]]:
    if not runner.conversation_id:
        return None
    user_id = runner._current_user_id()
    if not user_id:
        return None
    try:
        from app.services.ai.memory_service import memory_service

        return await memory_service.get_current_data_result(user_id, runner.conversation_id)
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to load last data result: %s", e)
        return None


async def load_last_data_result_with_retry(
    runner: Any,
    *,
    attempts: int = 3,
    delay_seconds: float = 0.15,
) -> Optional[Dict[str, Any]]:
    for attempt in range(attempts):
        result = await load_last_data_result(runner)
        if result:
            return result
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)
    return None


def normalize_rows_for_followup_save(parsed_tool_output: Any) -> Any:
    if isinstance(parsed_tool_output, list):
        return parsed_tool_output
    if isinstance(parsed_tool_output, dict):
        return parsed_tool_output
    return None


def latest_data_assistant_excerpt(
    history: List[Dict[str, str]],
    *,
    max_chars: int = 12000,
) -> str:
    for msg in reversed(history[:-1] or history):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > max_chars:
            return content[:max_chars] + "\n... [对话展示过长已截断]"
        return content
    return ""


async def save_last_data_result_for_followups(
    runner: Any,
    tool_args: Dict[str, Any],
    parsed_tool_output: Any,
) -> None:
    normalized = normalize_rows_for_followup_save(parsed_tool_output)
    if not runner.conversation_id or normalized is None:
        return
    user_id = runner._current_user_id()
    if not user_id:
        return
    observed_at = datetime.now(timezone.utc).isoformat()
    data_as_of = (
        normalized.get("data_as_of") or normalized.get("as_of")
        if isinstance(normalized, dict)
        else None
    )
    source_ref = (
        f"dataset://{tool_args.get('dataset_name')}"
        if tool_args.get("dataset_name")
        else None
    )
    payload = {
        "sql": tool_args.get("sql") or tool_args.get("query"),
        "data_source": tool_args.get("data_source"),
        "dataset_name": tool_args.get("dataset_name"),
        "rows": normalized,
        "saved_at": datetime.now().isoformat(),
        "observed_at": observed_at,
        "freshness": "dynamic",
        "source_ref": source_ref,
        "data_as_of": data_as_of,
        "result_status": _result_status(normalized),
        "trace_id": runner.trace_id,
    }
    try:
        from app.services.ai.memory_service import memory_service
        from app.services.ai.chatbi_result_stack import ChatBIAnalysisContext, ChatBIResultRef
        from app.services.ai.data_query_semantic_intent import semantic_intent_to_dict

        await memory_service.set_last_data_result(user_id, runner.conversation_id, payload)
        semantic = semantic_intent_to_dict(getattr(runner, "_semantic_intent", None))
        stack = await memory_service.get_data_result_stack(user_id, runner.conversation_id)
        parent_result_id = str(stack[-1].get("result_id") or "") if stack else None
        analysis_context = ChatBIAnalysisContext(
            metrics=list(semantic.get("metrics") or []),
            dimensions=list(semantic.get("dimensions") or []),
            filters=list(semantic.get("filters") or []),
            time_range={"expression": semantic.get("time_range")} if semantic.get("time_range") else {},
            time_grain=str(semantic.get("grain") or ""),
        )
        result_ref = ChatBIResultRef(
            parent_result_id=parent_result_id or None,
            question=str(getattr(runner, "_standalone_query", "") or ""),
            dataset_name=str(tool_args.get("dataset_name") or ""),
            data_source=str(tool_args.get("data_source") or ""),
            sql=str(tool_args.get("sql") or tool_args.get("query") or ""),
            rows=normalized,
            analysis_context=analysis_context,
            trace_id=str(runner.trace_id or ""),
            observed_at=observed_at,
            data_as_of=data_as_of,
            freshness="dynamic",
            source_ref=source_ref,
            result_status=payload["result_status"],
        )
        await memory_service.push_data_result_ref(
            user_id,
            runner.conversation_id,
            result_ref.to_dict(),
        )
        state = runner._last_run_state
        if state is not None:
            state.followup_data_saved = True
            state.current_result_id = result_ref.result_id
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to save last data result: %s", e)
