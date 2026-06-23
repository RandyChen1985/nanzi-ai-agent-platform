"""ChatBI turn routing — early-exit turns and federated query handoff."""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services.ai.data_query_turn_classifier import DataQueryTurnType, history_shows_recent_data_result
from app.services.ai.runners.chatbi.federated_upgrade import (
    extract_schema_dataset_names,
    should_upgrade_to_federated_query,
)
from app.services.ai.runners.chatbi.run_state import DataRunState

logger = logging.getLogger(__name__)

EARLY_EXIT_TURN_TYPES = frozenset(
    {
        DataQueryTurnType.FORMAT_CORRECTION,
        DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        DataQueryTurnType.CLARIFICATION_OR_NON_DATA,
    }
)


async def dispatch_early_turn(
    runner: Any,
    *,
    turn_cls: Any,
    history: List[Dict[str, str]],
    runtime_messages: List[Any],
    user_question: str,
    last_data_result_for_turn: Optional[Dict[str, Any]],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Handle turns that exit before tool/ReAct execution."""
    if turn_cls.turn_type == DataQueryTurnType.FORMAT_CORRECTION:
        if not last_data_result_for_turn:
            last_data_result_for_turn = await runner._load_last_data_result_with_retry()
        if last_data_result_for_turn:
            async for chunk in runner._synthesize_format_correction(
                runtime_messages,
                runner.config.system_prompt or "",
                user_question,
                last_data_result_for_turn,
            ):
                yield chunk
        else:
            async for chunk in runner._yield_missing_reusable_result_clarification(
                history,
                user_question=user_question,
            ):
                yield chunk
        return

    if turn_cls.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
        if not last_data_result_for_turn:
            last_data_result_for_turn = await runner._load_last_data_result_with_retry()
        if last_data_result_for_turn:
            async for chunk in runner._synthesize_from_last_data_result(
                runtime_messages,
                runner.config.system_prompt or "",
                user_question,
                last_data_result_for_turn,
            ):
                yield chunk
        elif history_shows_recent_data_result(history):
            async for chunk in runner._synthesize_from_history_data_result(
                runtime_messages,
                runner.config.system_prompt or "",
                user_question,
                history,
            ):
                yield chunk
        else:
            async for chunk in runner._yield_missing_reusable_result_clarification(
                history,
                user_question=user_question,
            ):
                yield chunk
        return

    if turn_cls.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA:
        async for chunk in runner._yield_contextual_clarification(
            user_question=user_question,
            history=history,
            reasoning=turn_cls.reasoning,
        ):
            yield chunk


async def run_federated_prefetch_upgrade(
    runner: Any,
    *,
    turn_cls: Any,
    prefetched_schema_output: str,
    system_content: str,
    runtime_messages: List[Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run federated executor when schema prefetch indicates cross-dataset query."""
    datasets = sorted(extract_schema_dataset_names(prefetched_schema_output))
    classified_as_federated = (
        turn_cls.turn_type == DataQueryTurnType.FEDERATED_DATA_QUERY and len(datasets) > 1
    )
    if not classified_as_federated and not should_upgrade_to_federated_query(
        prefetched_schema_output,
        runner._standalone_query,
    ):
        return

    turn_cls.turn_type = DataQueryTurnType.FEDERATED_DATA_QUERY
    yield {
        "type": "log",
        "id": f"chatbi_turn_upgrade_{uuid.uuid4().hex[:8]}",
        "title": "ChatBI 请求类别升级",
        "details": (
            f"检测到请求涉及跨数据集联合查询 (共 {len(datasets)} 个数据集: "
            f"{', '.join(datasets)})，已进入跨数据集联邦查询。"
        ),
        "status": "success",
        "category": "intent",
        "turn_type": turn_cls.turn_type.value,
        "execution_time_ms": 0,
    }
    runner._last_run_state = DataRunState(requires_fresh_data=True)
    from app.services.ai.chatbi_sql_query_binding import build_sql_query_binding
    from app.services.ai.executors.federated_executor import FederatedQueryExecutor
    from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

    sql_query_binding = build_sql_query_binding(schema_output=prefetched_schema_output)
    executor = FederatedQueryExecutor(
        agent_runner=runner,
        schema_output=prefetched_schema_output,
        datasets=datasets,
        sql_query_binding=sql_query_binding,
    )
    federated_content = ""
    async for chunk in executor.execute(
        runtime_messages=runtime_messages,
        system_prompt=system_content,
        user_question=runner._standalone_query,
    ):
        if chunk.get("content"):
            federated_content += chunk["content"]
        yield chunk
    if federated_content:
        deduped = finalize_visible_reply(federated_content)
        if deduped != federated_content:
            logger.warning(
                "[DataAgentRunner][Federated] Collapsed duplicated content (%d -> %d chars)",
                len(federated_content),
                len(deduped),
            )
            yield {"type": "retraction", "content": deduped}


async def run_federated_sql_upgrade(
    runner: Any,
    *,
    turn_cls: Any,
    exc: Any,
    prefetched_schema_output: str | None,
    system_content: str,
    runtime_messages: List[Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run federated executor after SQL triggers cross-dataset upgrade."""
    turn_cls.turn_type = DataQueryTurnType.FEDERATED_DATA_QUERY
    yield {
        "type": "log",
        "id": f"chatbi_turn_upgrade_{uuid.uuid4().hex[:8]}",
        "title": "ChatBI 请求类别升级",
        "details": (
            f"检测到 SQL 触发跨数据集限制，自动升级为联邦查询。"
            f"涉及数据集: {', '.join(sorted(exc.datasets))}"
        ),
        "status": "success",
        "category": "intent",
        "turn_type": turn_cls.turn_type.value,
        "execution_time_ms": 0,
    }
    schema_output = prefetched_schema_output or ""
    if not schema_output.strip():
        from app.core.orm import AsyncSessionLocal
        from app.services.chatbi_dataset_schema_service import fetch_dataset_schema_core

        async with AsyncSessionLocal() as session:
            schema_output = await fetch_dataset_schema_core(
                session,
                keywords=", ".join(sorted(exc.datasets)),
                user_id=runner._runtime_user_id(),
                is_admin=bool(runner.user_info.get("is_admin") if runner.user_info else False),
                api_key=None,
            )
    else:
        logger.info(
            "[DataAgentRunner][Federated] UpgradeToFederatedQuery: 复用已有 prefetched schema (%d chars)，跳过重新拉取。",
            len(schema_output),
        )
    runner._last_run_state = DataRunState(requires_fresh_data=True)
    from app.services.ai.chatbi_sql_query_binding import build_sql_query_binding
    from app.services.ai.executors.federated_executor import FederatedQueryExecutor
    from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

    sql_query_binding = getattr(exc, "binding", None)
    if sql_query_binding is None:
        sql_query_binding = build_sql_query_binding(
            schema_output=schema_output,
            sql=getattr(exc, "sql", "") or "",
        )
    elif schema_output:
        sql_query_binding.schema_output = schema_output
        for key, table_binding in build_sql_query_binding(schema_output=schema_output).tables.items():
            current = sql_query_binding.tables.get(key)
            if current is None:
                sql_query_binding.tables[key] = table_binding
            else:
                if not current.dataset_name:
                    current.dataset_name = table_binding.dataset_name
                if not current.data_source:
                    current.data_source = table_binding.data_source
                if not current.columns:
                    current.columns = list(table_binding.columns)

    executor = FederatedQueryExecutor(
        agent_runner=runner,
        schema_output=schema_output,
        datasets=sorted(list(exc.datasets)),
        sql_query_binding=sql_query_binding,
    )
    federated_content = ""
    async for chunk in executor.execute(
        runtime_messages=runtime_messages,
        system_prompt=system_content,
        user_question=runner._standalone_query,
    ):
        if chunk.get("content"):
            federated_content += chunk["content"]
        yield chunk
    if federated_content:
        deduped = finalize_visible_reply(federated_content)
        if deduped != federated_content:
            logger.warning(
                "[DataAgentRunner][Federated] Collapsed duplicated content (%d -> %d chars)",
                len(federated_content),
                len(deduped),
            )
            yield {"type": "retraction", "content": deduped}
