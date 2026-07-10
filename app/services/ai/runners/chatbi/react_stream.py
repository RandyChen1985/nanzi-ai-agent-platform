"""ChatBI react stream — extracted from DataAgentRunner."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep
from app.services.ai.chatbi_sql_user_messages import format_empty_filter_result_content, map_sql_tool_error_for_user
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk, map_standard_agentscope_event
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.platform_auto_retry import (
    format_platform_auto_retry_details,
    format_platform_auto_retry_title,
    platform_auto_retry_budget_exhausted,
    record_platform_auto_sql_attempt,
)

logger = logging.getLogger(__name__)


def _upgrade_to_federated_query_exc():
    from app.services.ai.runners.data_agent_runner import UpgradeToFederatedQuery
    return UpgradeToFederatedQuery


async def stream_agentscope_events(
    runner: Any,
    *,
    event_stream: Any,
    agent: Any | None = None,
    tools: list[RuntimeToolSpec],
    native_model: Any,
    state: DataRunState | None = None,
    stream_meta: Dict[str, Any] | None = None,
    emit_final_guard: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    state = state or DataRunState()
    stream_meta = stream_meta or {}
    runner._last_run_state = state
    stream_state = runner._build_stream_state(state, stream_meta)

    async def on_before_pending_interrupt(pending_state: Dict[str, Any]) -> None:
        runner._sync_pending_data_run_state(state, pending_state)

    async def on_tool_result_end(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
        tool_id = getattr(event, "tool_call_id", "")
        tool_name = state.tool_names.get(tool_id, "")
        raw_args = state.tool_args_text.get(tool_id, "") or "{}"
        try:
            tool_args = json.loads(raw_args)
        except Exception:
            tool_args = {"input": raw_args}
        output = state.tool_outputs.get(tool_id, "")
        duration_ms = (time.time() - state.tool_started_at.get(tool_id, time.time())) * 1000
        if tool_name == "get_dataset_schema":
            runner._record_schema_keywords(state, tool_args)
            runner._apply_schema_tool_result(state, output)
            if state.schema_miss:
                runner._prepare_controlled_schema_retry_keywords(
                    state,
                    str(stream_meta.get("user_question") or ""),
                )
        elif tool_name == "execute_sql_query":
            parsed_output, should_save_followup = runner._apply_sql_tool_result(
                state,
                tool_args=tool_args,
                output=output,
            )
            auto_retry = None
            if state.empty_sql_result and not platform_auto_retry_budget_exhausted(state):
                auto_retry = await runner._maybe_run_empty_filter_diagnostics(state, tool_args=tool_args)
            where_retry = None
            if state.sql_error and not platform_auto_retry_budget_exhausted(state):
                where_retry = await runner._maybe_run_where_condition_diagnostics(
                    state, tool_args=tool_args
                )
                if where_retry and state.where_condition_diagnostic_summary:
                    yield {
                        "type": "log",
                        "id": f"{tool_id}:where_condition_probe",
                        "title": "平台自动探查 WHERE 字段样例",
                        "details": state.where_condition_diagnostic_summary,
                        "status": "success" if where_retry.has_rows else "warning",
                        "execution_time_ms": 0,
                    }
            if where_retry and where_retry.has_rows:
                attempt = record_platform_auto_sql_attempt(state)
                should_save_followup = runner._apply_auto_retry_sql_result(
                    state,
                    sql_text=where_retry.corrected_sql,
                    output=where_retry.raw_output,
                    parsed_output=where_retry.parsed_output,
                )
                output = where_retry.raw_output
                parsed_output = where_retry.parsed_output
                state.tool_outputs[tool_id] = output
                state.sql_error = False
                state.sql_error_message = ""
                yield {
                    "type": "log",
                    "id": f"{tool_id}:where_condition_auto_retry",
                    "title": format_platform_auto_retry_title("平台自动修正 WHERE 并重试", attempt),
                    "details": format_platform_auto_retry_details(
                        (
                            f"{where_retry.summary}\n\n```sql\n{where_retry.corrected_sql}\n```"
                            if where_retry.corrected_sql
                            else where_retry.summary
                        ),
                        attempt,
                    ),
                    "status": "success",
                    "execution_time_ms": 0,
                }
            elif where_retry and where_retry.attempted:
                attempt = record_platform_auto_sql_attempt(state)
                yield {
                    "type": "log",
                    "id": f"{tool_id}:where_condition_auto_retry",
                    "title": format_platform_auto_retry_title("平台自动修正 WHERE 并重试", attempt),
                    "details": format_platform_auto_retry_details(
                        (
                            f"{where_retry.summary}\n\n```sql\n{where_retry.corrected_sql}\n```"
                            if where_retry.corrected_sql
                            else where_retry.summary
                        ),
                        attempt,
                    ),
                    "status": "warning",
                    "execution_time_ms": 0,
                }
            if auto_retry and auto_retry.has_rows:
                attempt = record_platform_auto_sql_attempt(state)
                should_save_followup = runner._apply_auto_retry_sql_result(
                    state,
                    sql_text=auto_retry.corrected_sql,
                    output=auto_retry.raw_output,
                    parsed_output=auto_retry.parsed_output,
                )
                output = auto_retry.raw_output
                parsed_output = auto_retry.parsed_output
                state.tool_outputs[tool_id] = output
                yield {
                    "type": "log",
                    "id": f"{tool_id}:empty_filter_auto_retry",
                    "title": format_platform_auto_retry_title("平台自动修正筛选并重试", attempt),
                    "details": format_platform_auto_retry_details(
                        (
                            f"{auto_retry.summary}\n\n```sql\n{auto_retry.corrected_sql}\n```"
                            if auto_retry.corrected_sql
                            else auto_retry.summary
                        ),
                        attempt,
                    ),
                    "status": "success",
                    "execution_time_ms": 0,
                }
            elif auto_retry and auto_retry.attempted:
                attempt = record_platform_auto_sql_attempt(state)
                if auto_retry.corrected_sql:
                    state.empty_sql_text = auto_retry.corrected_sql
                yield {
                    "type": "log",
                    "id": f"{tool_id}:empty_filter_auto_retry",
                    "title": format_platform_auto_retry_title("平台自动修正筛选并重试", attempt),
                    "details": format_platform_auto_retry_details(
                        (
                            f"{auto_retry.summary}\n\n```sql\n{auto_retry.corrected_sql}\n```"
                            if auto_retry.corrected_sql
                            else auto_retry.summary
                        ),
                        attempt,
                    ),
                    "status": "warning",
                    "execution_time_ms": 0,
                }
            if state.sql_error and "不属于当前指定的数据集" in (state.sql_error_message or ""):
                from app.services.ai.chatbi_sql_query_binding import build_federated_upgrade_binding
                from app.services.sql_query_execution_service import dialect_from_data_source
                from app.core.orm import AsyncSessionLocal

                sql = tool_args.get("sql", "")
                dialect = dialect_from_data_source(tool_args.get("data_source", ""))
                binding = None
                datasets: set[str] = set()
                async with AsyncSessionLocal() as session:
                    binding = await build_federated_upgrade_binding(
                        session,
                        sql=sql,
                        dialect=dialect,
                        schema_output=state.schema_output,
                        schema_bindings=state.table_bindings,
                        primary_dataset_name=str(tool_args.get("dataset_name") or ""),
                    )
                    datasets = binding.involved_datasets()

                if len(datasets) > 1:
                    UpgradeToFederatedQuery = _upgrade_to_federated_query_exc()
                    state.sql_query_binding = binding
                    raise UpgradeToFederatedQuery(sql=sql, datasets=datasets, binding=binding)
            enrichment_result = None
            if should_save_followup:
                output, parsed_output, enrichment_result = await runner._maybe_enrich_sql_tool_result(
                    tool_args=tool_args,
                    output=output,
                    parsed_output=parsed_output,
                )
                state.tool_outputs[tool_id] = output
                state.last_successful_sql_output = output
                await runner._save_last_data_result_for_followups(tool_args, parsed_output)
                if enrichment_result is not None and getattr(enrichment_result, "applied", False):
                    runner._increment_step()
                    enrichment_details = "\n".join(getattr(enrichment_result, "logs", []) or [])
                    runner.trace_buffer.append(
                        AgentExecutionStep(
                            step_number=runner.step_counter,
                            event_type="tool_call",
                            agent_name=runner.config.agent_name,
                            model=getattr(native_model, "model", runner.config.model_name),
                            temperature=float(runner.config.temperature or 0),
                            tool_name="dimension_enrichment",
                            tool_input={"dataset_name": tool_args.get("dataset_name")},
                            tool_output={"logs": getattr(enrichment_result, "logs", [])},
                            raw_log=enrichment_details,
                            execution_time_ms=0,
                            timestamp=datetime.now(),
                        )
                    )
                    yield {
                        "type": "log",
                        "id": f"{tool_id}:dimension_enrichment",
                        "title": "跨数据集维度补全",
                        "details": enrichment_details or "已根据 relation 补全跨数据集维度字段。",
                        "status": "success",
                        "execution_time_ms": 0,
                    }
                citation_args = dict(tool_args)
                if auto_retry and getattr(auto_retry, "corrected_sql", None):
                    citation_args["sql"] = auto_retry.corrected_sql
                state.last_successful_sql_args = citation_args
                from app.services.ai.chatbi_citation_utils import maybe_build_chatbi_sql_citation_event

                citation_event = maybe_build_chatbi_sql_citation_event(
                    state,
                    tool_call_id=tool_id,
                    tool_args=citation_args,
                    parsed_output=parsed_output,
                )
                if citation_event:
                    yield citation_event
        runner._record_tool_call_signature(state, tool_name, tool_args)
        state.halt_current_react = (
            state.sql_error
            or state.empty_sql_result
            or state.sql_plan_missing
            or state.sql_static_risk
            or state.time_range_anomaly
            or state.sql_sandbox_blocked
            or state.sql_repeat_gate_block
            or state.failed_sql_repeat_gate_block
            or state.duration_anomaly
            or state.diagnostic_sql_pending_final
            or state.tool_loop_fuse_triggered
            or runner._is_schema_fatal(state)
        )
        runner._sync_pending_data_run_state(state, stream_state)
        runner._increment_step()
        runner.trace_buffer.append(
            AgentExecutionStep(
                step_number=runner.step_counter,
                event_type="tool_call",
                agent_name=runner.config.agent_name,
                model=getattr(native_model, "model", runner.config.model_name),
                temperature=float(runner.config.temperature or 0),
                tool_name=tool_name,
                tool_input=tool_args,
                tool_output=output,
                raw_log=str(output),
                execution_time_ms=duration_ms,
                timestamp=datetime.fromtimestamp(state.tool_started_at.get(tool_id, time.time())),
            )
        )
        notice = None
        if tool_name == "execute_sql_query" and not state.sql_error:
            final_parsed = runner._try_parse_json_output(output)
            notice = final_parsed.get("permission_notice") if isinstance(final_parsed, dict) else None
            if isinstance(notice, dict) and notice.get("row_filter_applied") is True:
                yield {"type": "meta", "permission_notice": notice}
        is_error = (
            "Error" in str(output)
            or "安全策略拦截" in str(output)
            or "Permission Denied" in str(output)
            or "PermissionDenied" in str(output)
            or getattr(state, "sql_error", False)
        )
        log_payload: Dict[str, Any] = {
            "type": "log",
            "id": tool_id,
            "title": f"工具完成: {tool_name}",
            "details": runner._format_tool_details(tool_name, output, state, tool_args),
            "status": "success" if not is_error else "error",
            "execution_time_ms": duration_ms,
        }
        if (
            tool_name == "execute_sql_query"
            and isinstance(notice, dict)
            and notice.get("row_filter_applied") is True
        ):
            log_payload["row_filter_applied"] = True
        yield log_payload

    def track_sql_plan_delta(delta: str) -> None:
        state.text_window = (state.text_window + delta)[-4000:]
        if runner._has_sql_plan(state.text_window):
            if not state.sql_plan_seen:
                state.sql_plan_seen = True
                runner._sync_pending_data_run_state(state, stream_state)

    async def on_text_block_delta(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
        block_id = str(getattr(event, "block_id", "") or "")
        if block_id:
            state.active_text_block_id = block_id
        if state.ignore_text_block:
            return
        delta = str(getattr(event, "delta", ""))
        track_sql_plan_delta(delta)
        if not state.ready_to_answer:
            state.blocked_content += delta
            return
        if not state.content_emitted:
            state.content_emitted = True
            yield {
                "type": "log",
                "id": f"gen_data_{uuid.uuid4().hex[:8]}",
                "title": "✨ 开始生成回复",
                "status": "success",
            }
        state.full_content += delta
        state.current_text_block_emitted = True
        yield {"content": delta}

    async for event in event_stream:
        event_type = str(getattr(event, "type", ""))
        if event_type == "MODEL_CALL_END":
            runner._record_agent_scope_model_call(
                event,
                state=stream_state,
                native_model=native_model,
            )
        if event_type == "THINKING_BLOCK_DELTA":
            track_sql_plan_delta(str(getattr(event, "delta", "")))
        if event_type == "TOOL_CALL_START":
            state.text_blocks_emitted_since_last_tool = 0
            state.ignore_text_block = False
            state.current_text_block_emitted = False

        if event_type == "TEXT_BLOCK_START":
            block_id = str(getattr(event, "block_id", "") or "")
            if block_id:
                state.active_text_block_id = block_id
            state.current_text_block_emitted = False
            state.ignore_text_block = (
                state.ready_to_answer
                and state.text_blocks_emitted_since_last_tool >= 1
            )
            continue

        if event_type == "TEXT_BLOCK_END":
            if state.current_text_block_emitted and state.ready_to_answer:
                state.text_blocks_emitted_since_last_tool += 1
            state.current_text_block_emitted = False
            continue

        async for chunk in map_standard_agentscope_event(
            event,
            state=stream_state,
            on_tool_result_end=on_tool_result_end,
            on_text_block_delta=on_text_block_delta,
            on_before_pending_interrupt=on_before_pending_interrupt,
            agent=agent,
            runner=runner,
            tools=tools,
            native_model=native_model,
            agent_name=runner._runtime_agent_name(),
        ):
            yield chunk
            if is_interrupt_sse_chunk(chunk):
                return
        if state.sql_fatal_error:
            logger.info("[DataAgentRunner] Fatal SQL error detected during ReAct. Terminating execution immediately.")
            async for chunk in runner._yield_sql_fatal_abort(state):
                yield chunk
            return
        if state.halt_current_react:
            logger.info("[DataAgentRunner] SQL result requires repair. Stopping current ReAct stream.")
            if state.full_content and runner._current_repair_kind(state):
                async for chunk in runner._retract_provisional_content_before_repair(
                    state,
                    reason="halt after SQL tool result requires repair",
                ):
                    yield chunk
            break

    if emit_final_guard:
        guard_emitted = False
        async for chunk in runner._emit_final_guard(state):
            guard_emitted = True
            yield chunk
        if guard_emitted:
            return

    if state.full_content:
        runner._increment_step()
        runner.trace_buffer.append(
            AgentExecutionStep(
                step_number=runner.step_counter,
                event_type="synthesis",
                agent_name=runner.config.agent_name,
                model=getattr(native_model, "model", runner.config.model_name),
                temperature=float(runner.config.temperature or 0),
                tool_output={"content": state.full_content},
                raw_log=state.full_content,
                execution_time_ms=(time.time() - state.start_synthesis) * 1000,
                timestamp=datetime.fromtimestamp(state.start_synthesis),
            )
        )

async def emit_final_guard(
    runner: Any,
    state: DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    has_guard_condition = (
        bool(state.blocked_content)
        or state.sql_before_schema
        or state.sql_error
        or state.empty_sql_result
        or state.sql_static_risk
        or state.time_range_anomaly
        or state.failed_sql_repeat_gate_block
        or state.duration_anomaly
        or state.diagnostic_sql_pending_final
        or state.tool_loop_fuse_triggered
        or state.sql_sandbox_blocked
        or runner._is_schema_fatal(state)
    )
    if state.full_content or state.ready_to_answer or not has_guard_condition:
        return
    if runner._is_schema_fatal(state):
        _, content = runner._schema_fatal_response(state)
    elif (
        state.requires_fresh_data
        and state.requires_sql_query
        and not state.sql_completed
        and (
            state.schema_miss_count >= 2
            or (not state.schema_completed and state.schema_miss_count >= 1)
        )
    ):
        content = (
            DataQueryPrompts.SCHEMA_MISS_EXHAUSTED_CONTENT
            if state.schema_miss_count >= 2
            else DataQueryPrompts.SCHEMA_MISS_ABORT_CONTENT
        )
    elif state.sql_before_schema:
        content = "为保证数据准确性，请先检索数据集定义后再执行数据查询。"
    elif state.sql_error:
        error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
        presentation = map_sql_tool_error_for_user(error_text)
        content = presentation.content
    elif state.failed_sql_repeat_gate_block:
        error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
        presentation = map_sql_tool_error_for_user(error_text)
        content = presentation.content
    elif state.diagnostic_sql_pending_final:
        content = (
            "诊断查询已返回样本数据，但尚未完成最终业务 SQL 复核，暂时无法生成结论。\n\n"
            "💡 **建议您可以尝试**：\n"
            "1. 稍微调整筛选条件或时间范围后重新提问。\n"
            "2. 若问题较复杂，可拆成更具体的单指标/单表问题。"
        )
    elif state.empty_sql_result:
        content = format_empty_filter_result_content(state.empty_filter_diagnostics)
    elif state.sql_static_risk:
        content = (
            "该查询涉及的数据量过大或超出安全规范，已被系统自动拦截。\n\n"
            "💡 **建议您可以尝试**：\n"
            "1. 明确时间限制（如“查询最近3天”、“本周内”）。\n"
            "2. 避免使用过于宽泛的“全部”或“所有”类型查询，缩小范围后重试。"
        )
    elif state.time_range_anomaly:
        content = (
            "查询 SQL 中的时间范围与您问题里的相对时间不一致，已被系统拦截。\n\n"
            f"原因：{state.time_range_anomaly_reason}\n\n"
            "💡 **建议**：请确认问题中的时间表述（如「上个月」「本月」），系统将按当前日期自动换算后再查数。"
        )
    elif state.sql_sandbox_blocked:
        content = (
            "该查询因性能或安全风险已被系统前置网关自动拦截。\n\n"
            "💡 **建议您可以尝试**：\n"
            f"1. {state.sql_sandbox_blocked_reason}\n"
            "2. 精确限定时间范围（如“查询最近3天”、“本周内”）以降低扫描数据量。"
        )
    elif state.duration_anomaly:
        content = (
            "计算出的时延/时长数据疑似存在异常，为保证数据准确性，已自动拦截该次回答。\n\n"
            "💡 **建议您可以尝试**：\n"
            "1. 重新检查提问中涉及的时间字段方向、时区或单位定义。\n"
            "2. 重新核对提问内容并以简化的表述重新提问。"
        )
    elif state.tool_loop_fuse_triggered:
        content = f"检测到工具调用出现循环，已被系统安全中止。{state.tool_loop_fuse_reason}"
    else:
        content = "由于未能完成有效的数据检索和计算，无法为您生成准确的回答。建议您核对问题后重新提问。"
    yield {
        "type": "log",
        "id": f"data_guard_{uuid.uuid4().hex[:8]}",
        "title": "阻止未查数回答",
        "details": "模型在满足 ChatBI 查数顺序前尝试直接回答，已拦截该输出。",
        "status": "warning",
    }
    yield {
        "content": content,
        "status": "error",
    }

async def yield_sql_fatal_abort(
    runner: Any,
    state: DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    if state.sql_fatal_emitted:
        return
    presentation = map_sql_tool_error_for_user(state.sql_fatal_message)
    state.sql_fatal_emitted = True
    yield {
        "type": "log",
        "id": f"fatal_sql_{uuid.uuid4().hex[:8]}",
        "title": presentation.title,
        "details": truncate_for_context(str(state.sql_fatal_message or ""), max_len=1000)
        or presentation.title,
        "status": "error",
    }
    yield {
        "content": presentation.content,
        "status": "error",
    }

async def yield_schema_fatal_abort(
    runner: Any,
    state: DataRunState,
    details: Any = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
    title, content = runner._schema_fatal_response(state)
    yield {
        "type": "log",
        "id": f"schema_fatal_{uuid.uuid4().hex[:8]}",
        "title": title,
        "details": truncate_for_context(str(details or ""), max_len=1000) or title,
        "status": "error",
    }
    yield {
        "content": content,
        "status": "error",
    }

async def retract_provisional_content_before_repair(
    runner: Any,
    state: DataRunState,
    *,
    reason: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
    if not state.full_content:
        return
    logger.info(
        "[DataAgentRunner] Retracting provisional content before continuing repair: %s",
        reason,
    )
    state.full_content = ""
    state.content_emitted = False
    state.current_text_block_emitted = False
    state.text_blocks_emitted_since_last_tool = 0
    yield {
        "type": "retraction",
        "content": "",
        "final": False,
    }

