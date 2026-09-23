"""ChatBI react stream — extracted from DataAgentRunner."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict

from app.schemas.agent import AgentExecutionStep
from app.services.ai.chatbi_sql_user_messages import format_empty_filter_result_content, map_sql_tool_error_for_user
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk, map_standard_agentscope_event
from app.services.ai.runtime.agentscope.tool_result import (
    extract_tool_result_error_reason,
    is_tool_result_error,
    normalize_tool_result_state,
)
from app.services.ai.runtime.agentscope.stream_reconcile import (
    format_tool_args_for_display,
    truncate_for_display,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runners.assistant_agent_runner import (
    _build_file_tool_metadata,
    _resolve_agentscope_tool_args,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.sql_result_compact import (
    mark_successful_nonempty_sql,
    mark_visible_content_emitted,
)
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
    repetition_detector = getattr(state, "repetition_detector", None)
    if repetition_detector is not None:
        # 同一个 DataRunState 可能被初始 ReAct 流和 repair 流复用；重复检测
        # 只应覆盖当前模型流，不能把上一段流的计数带入下一段。
        repetition_detector.reset()
    stream_state = runner._build_stream_state(state, stream_meta)
    execution_backend = getattr(runner, "_execution_backend", None)
    if execution_backend:
        stream_state.setdefault("execution_backend", execution_backend)

    async def on_before_pending_interrupt(pending_state: Dict[str, Any]) -> None:
        runner._sync_pending_data_run_state(state, pending_state)

    async def on_tool_result_end(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
        tool_id = getattr(event, "tool_call_id", "")
        tool_name = state.tool_names.get(tool_id, "")
        raw_args = state.tool_args_text.get(tool_id, "")
        tool_args = _resolve_agentscope_tool_args(agent, tool_id, raw_args)
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
            # 工具层可能已向模型回传抽样结果；完整结果在 pending 中，供落库/enrich/依据卡。
            full_output = getattr(state, "pending_sql_tool_full_output", None)
            if full_output is not None:
                output = full_output
                state.tool_outputs[tool_id] = full_output
                state.pending_sql_tool_full_output = None
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
                state.platform_auto_retry_ready = True
                mark_successful_nonempty_sql(state, tool_name=tool_name)
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
                state.platform_auto_retry_ready = True
                mark_successful_nonempty_sql(state, tool_name=tool_name)
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
                mark_successful_nonempty_sql(state, tool_name=tool_name)
                saved_meta = await runner._save_last_data_result_for_followups(tool_args, parsed_output)
                if saved_meta:
                    from app.services.ai.reusable_result import build_reusable_result_status_event

                    yield build_reusable_result_status_event(
                        status="saved",
                        payload=saved_meta,
                    )
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
            or state.platform_auto_retry_ready
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
        tool_result_state = (
            stream_state.get("tool_result_states", {}).get(tool_id)
            or getattr(event, "state", None)
        )
        is_error = is_tool_result_error(
            tool_name,
            output,
            result_state=tool_result_state,
            domain_error=bool(getattr(state, "sql_error", False)),
        )
        log_payload: Dict[str, Any] = {
            "type": "log",
            "id": tool_id,
            "title": f"工具完成: {tool_name}",
            "details": runner._format_tool_details(tool_name, output, state, tool_args),
            "status": "success" if not is_error else "error",
            "execution_time_ms": duration_ms,
        }
        # 工具入参（如 SQL、数据源）单独透传，不占用 details 的输出预算。
        display_args = format_tool_args_for_display(tool_args, tool_name=tool_name)
        if display_args:
            log_payload["tool_args"] = display_args
        normalized_result_state = normalize_tool_result_state(tool_result_state)
        if normalized_result_state:
            log_payload["tool_result_state"] = normalized_result_state
        error_reason = extract_tool_result_error_reason(
            tool_name,
            getattr(state, "sql_error_message", "") or output,
            result_state=tool_result_state,
            domain_error=bool(getattr(state, "sql_error", False)),
        )
        if error_reason:
            log_payload["error_reason"] = error_reason
        file_metadata = _build_file_tool_metadata(tool_name, tool_args, output)
        if file_metadata:
            log_payload["file_metadata"] = file_metadata
        if (
            tool_name == "execute_sql_query"
            and isinstance(notice, dict)
            and notice.get("row_filter_applied") is True
        ):
            log_payload["row_filter_applied"] = True
        yield log_payload

        from app.services.ai.business_confirmation import build_business_confirmation_sse
        from app.services.ai.user_question import build_user_question_sse, persist_user_question_event

        if not is_error:
            confirmation_event = build_business_confirmation_sse(
                tool_name=tool_name,
                tool_output=output,
                tool_call_id=tool_id,
            )
            if confirmation_event:
                yield confirmation_event
            question_event = build_user_question_sse(
                tool_name=tool_name,
                tool_output=output,
                tool_call_id=tool_id,
            )
            if question_event:
                try:
                    await persist_user_question_event(
                        event=question_event,
                        user_id=runner._runtime_user_id(),
                        conversation_id=runner.conversation_id or "",
                    )
                except Exception:
                    logger.exception("Failed to persist pending user question")
                    yield {
                        "type": "error",
                        "status": "error",
                        "content": "无法保存待回答问题，请稍后重试。",
                    }
                    return
                yield question_event

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
        if not delta:
            return

        repetition_detector = getattr(state, "repetition_detector", None)
        if repetition_detector is None:
            from app.services.ai.runtime.stream_repetition_detector import StreamRepetitionDetector

            repetition_detector = StreamRepetitionDetector()
            setattr(state, "repetition_detector", repetition_detector)

        if repetition_detector.is_fused:
            return

        verdict = repetition_detector.feed(delta)
        if verdict.fused:
            error_msg = (
                f"\n\n⚠️ [流式安全拦截] {verdict.message}"
                "建议重新发起提问或切换更稳定的旗舰模型（如 DeepSeek-Chat / Claude）。"
            )
            yield {"type": "error", "status": "error", "content": error_msg}
            return

        track_sql_plan_delta(delta)
        if not state.ready_to_answer:
            state.blocked_content += delta
            return
        if not state.content_emitted:
            state.content_emitted = True
            yield {
                "type": "log",
                "id": f"gen_data_{uuid.uuid4().hex[:8]}",
                "title": "开始生成回复",
                "status": "success",
            }
        state.full_content += delta
        state.current_text_block_emitted = True
        mark_visible_content_emitted(state)
        yield {"content": delta}

    async for event in event_stream:
        event_type = str(getattr(event, "type", ""))
        if event_type == "MODEL_CALL_START":
            detector = getattr(state, "repetition_detector", None)
            if detector is not None:
                detector.reset()
        if event_type == "MODEL_CALL_END":
            runner._record_agent_scope_model_call(
                event,
                state=stream_state,
                native_model=native_model,
            )
        if event_type == "THINKING_BLOCK_DELTA":
            track_sql_plan_delta(str(getattr(event, "delta", "")))
        if event_type == "TOOL_CALL_START":
            detector = getattr(state, "repetition_detector", None)
            if detector is not None:
                detector.reset()
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
    guard_title = "阻止未查数回答"
    guard_details = "模型在满足 ChatBI 查数顺序前尝试直接回答，已拦截该输出。"
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
        guard_title = "等待最终查数复核"
        guard_details = (
            "上一轮已返回诊断/探查样本数据，但最终业务 SQL 尚未完成；"
            "为避免用样本直接下结论，已拦截该回答。"
        )
        content = (
            "诊断查询已返回样本数据，但尚未完成最终业务 SQL 复核，暂时无法生成结论。"
            "这不代表没有查到数，而是还需要一次正式业务查询后才能回答。\n\n"
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
            "系统提示：查询结果中的时延/时长字段可能存在异常，仅作参考提示，未硬拦截结果。\n\n"
            f"原因：{state.duration_anomaly_reason or '时长类字段出现负值或极端延迟'}\n\n"
            "💡 **建议**：核对时间字段相减方向、时区与单位；若业务口径确认无误，可直接使用当前结果并在解读中说明疑点。"
        )
    elif state.tool_loop_fuse_triggered:
        content = f"检测到工具调用出现循环，已被系统安全中止。{state.tool_loop_fuse_reason}"
    else:
        content = "由于未能完成有效的数据检索和计算，无法为您生成准确的回答。建议您核对问题后重新提问。"
    yield {
        "type": "log",
        "id": f"data_guard_{uuid.uuid4().hex[:8]}",
        "title": guard_title,
        "details": guard_details,
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
        "details": truncate_for_display(str(state.sql_fatal_message or ""), max_len=1000)
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
    if state.schema_miss_count >= 2 and not state.no_authorized_schema:
        from app.services.ai.runners.chatbi.handoff import stream_to_routed_assistant
        from app.services.ai.runners.chatbi.source_reclassification import (
            SchemaMissDisposition,
            reclassify_schema_miss_source,
        )

        query = str(getattr(runner, "_standalone_query", "") or "").strip()
        source_decision = reclassify_schema_miss_source(query)
        if source_decision.disposition != SchemaMissDisposition.KEEP_DATA_FAILURE:
            yield {
                "type": "log",
                "id": f"schema_source_{uuid.uuid4().hex[:8]}",
                "title": "重新判断请求来源",
                "details": (
                    "内部数据集连续未命中，已判断该请求更适合其他信息来源并继续处理"
                ),
                "status": "success",
                "category": "intent",
                "source": source_decision.source.value,
            }
            try:
                async for chunk in stream_to_routed_assistant(
                    runner,
                    history=list(getattr(runner, "_active_history", []) or []),
                    user_question=query,
                    reason=source_decision.reason,
                ):
                    yield chunk
                return
            except Exception as exc:
                logger.warning("[DataAgentRunner] Schema-miss source handoff failed: %s", exc)
    title, content = runner._schema_fatal_response(state)
    yield {
        "type": "log",
        "id": f"schema_fatal_{uuid.uuid4().hex[:8]}",
        "title": title,
        "details": truncate_for_display(str(details or ""), max_len=1000) or title,
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
    # 撤回可见正文后重置正文时序；保留成功 SQL 时序，便于后续走「工具后无收口」合成。
    state.last_visible_content_at = 0
    yield {
        "type": "retraction",
        "content": "",
        "final": False,
    }
