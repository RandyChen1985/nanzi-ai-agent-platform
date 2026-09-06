"""
FinalizeStep: 负责流式调用结束后的 Token 消耗聚合统计、终态状态机事件透传、
Assistant 消息持久化、会话摘要合并触发与审计日志记录。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import asyncio
import logging

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.audit import AuditManager, aggregate_tokens_from_trace_buffer
from app.core.cancellation import await_unless_cancelling, current_task_cancelling

logger = logging.getLogger(__name__)

# 不需要在此刻做完结审计的状态（等待外部恢复）
AWAITING_RESUME_STATUSES = {
    "interrupted",
    "awaiting_permission",
    "awaiting_external_execution",
    "awaiting_user",
}

# 原版（重构前）在 chat_completion_stream 外层早退、根本没有任何终结产物的状态。
# 恢复原行为：这些短路态不应产生 run_status 事件，也不应产生审计记录。
SHORT_CIRCUIT_NO_FINALIZE_STATUSES = {
    "empty_request",
    "no_agent_config",
    "quota_exceeded",
}


def _public_agent_type(agent_config: Any) -> Optional[str]:
    """Helper to resolve agent type safely."""
    raw = getattr(agent_config, "agent_type", None)
    return str(raw).strip() if raw is not None and str(raw).strip() else None


class FinalizeStep(BasePipelineStep):
    """Executes finalization logic after agent execution stream completes."""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    async def run(
        self, context: PipelineContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if context.user_question_cancelled:
            # 取消卡路径：执行状态已置为 cancelled（context_step 同步）。此前的实现在这里直接早退，
            # 导致不发 run_status / 不审计 / 不持久化 / 跳过 performance 快照，与 run_handle 取消路径
            # （走完整 finalize）终态收拢语义分裂。这里不再早退，交由下方统一完成收拢，
            # 并保证即使 content 为空也会发射 run_status=cancelled 以对齐取消语义。
            if not context.execution_status:
                context.execution_status = "cancelled"

        shared_state = context.shared_state or {}
        agent_config = shared_state.get("agent_config") or getattr(context, "agent_config", None)
        user_info = context.user_info
        trace_id = context.trace_id
        conversation_id = context.conversation_id
        audit_completed = False
        start_time = context.start_time

        try:
            # 1. Aggregate Tokens
            p_tokens, c_tokens, t_tokens = 0, 0, 0
            try:
                if context.trace_buffer:
                    p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(
                        context.trace_buffer
                    )
            except Exception as agg_err:
                logger.warning(f"Failed to aggregate tokens for session: {agg_err}")

            context.prompt_tokens = p_tokens
            context.completion_tokens = c_tokens
            context.total_tokens = t_tokens

            if p_tokens or c_tokens:
                yield {
                    "type": "meta",
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": t_tokens,
                }

            if context.has_data_output and context.execution_status == "success":
                yield {"type": "meta", "has_data_output": True}

            # 2. History persistence decision
            from app.services.ai.agent_service import (
                _filter_current_turn_download_urls,
                _finalize_todo_success,
                _final_process_timeline,
                _persist_assistant_message_and_summary,
                _should_persist_turn_history,
                AuditManager as AgentServiceAuditManager,
            )

            guarded_response_content = _filter_current_turn_download_urls(
                context.full_response_content
            )
            if guarded_response_content != context.full_response_content:
                context.full_response_content = guarded_response_content
                shared_state["full_response_content"] = guarded_response_content
                yield {
                    "type": "retraction",
                    "content": guarded_response_content,
                }

            todo_completion = _finalize_todo_success(
                shared_state.get("process_timeline"),
                execution_status=context.execution_status,
            )
            if todo_completion:
                yield todo_completion

            final_process_timeline = _final_process_timeline(
                shared_state.get("process_timeline")
            )
            should_persist = bool(
                conversation_id
                and context.execution_status != "answered_directly"
                and _should_persist_turn_history(
                    context.full_response_content,
                    final_process_timeline,
                    context.full_reasoning_content,
                )
            )

            if (
                context.execution_status not in SHORT_CIRCUIT_NO_FINALIZE_STATUSES
            ):
                yield {
                    "type": "run_status",
                    "status": context.execution_status,
                    "trace_id": trace_id,
                    "persisting": should_persist,
                }

            if should_persist:
                handled_by = (
                    getattr(agent_config, "agent_name", None) if agent_config else None
                )
                u_id = context.lane_user_id
                await await_unless_cancelling(
                    lambda: _persist_assistant_message_and_summary(
                        user_id=u_id,
                        conversation_id=conversation_id,
                        content=context.full_response_content,
                        trace_id=trace_id,
                        agent_name=handled_by,
                        agent_type=_public_agent_type(agent_config),
                        agent_display_name=(
                            getattr(agent_config, "agent_display_name", None) or None
                        ),
                        prompt_tokens=p_tokens,
                        completion_tokens=c_tokens,
                        total_tokens=t_tokens,
                        has_data_output=context.has_data_output or None,
                        reusable_result_id=shared_state.get("reusable_result_status", {}).get("result_id"),
                        reusable_result_status=shared_state.get("reusable_result_status", {}).get("status"),
                        reasoning_content=context.full_reasoning_content or None,
                        process_timeline=final_process_timeline,
                        tool_run_text=context.tool_run_text,
                        merge_summary=context.execution_status == "success",
                        defer_summary=True,
                        status=context.execution_status,
                    ),
                    name=f"persist-cancelled-turn-{conversation_id}",
                )

            is_scheduled_task = bool(user_info and user_info.get("is_scheduled_task"))
            if (
                context.execution_status not in AWAITING_RESUME_STATUSES
                and context.execution_status not in SHORT_CIRCUIT_NO_FINALIZE_STATUSES
            ) or is_scheduled_task:
                end_time = asyncio.get_running_loop().time()
                duration = (end_time - start_time) * 1000
                import app.services.ai.agent_service as agent_service_module
                AuditManagerClass = getattr(agent_service_module, "AuditManager", AgentServiceAuditManager)
                audit_detached = current_task_cancelling()
                await await_unless_cancelling(
                    lambda: AuditManagerClass.log_transaction(
                        trace_id,
                        agent_config,
                        context.user_query,
                        context.full_response_content,
                        user_info,
                        context.execution_status,
                        duration,
                        context.trace_buffer,
                        conversation_id=conversation_id,
                        reasoning_content=context.full_reasoning_content or None,
                        process_timeline=final_process_timeline,
                        has_data_output=(
                            context.has_data_output
                            if context.execution_status == "success"
                            else None
                        ),
                    ),
                    name=f"audit-cancelled-turn-{trace_id}",
                )
                audit_completed = not audit_detached
        finally:
            performance_tracker = context.performance_tracker or shared_state.get("performance_tracker")
            if audit_completed and performance_tracker is not None:
                performance_tracker.mark("audit_finish")
            if performance_tracker is not None:
                performance_snapshot = performance_tracker.snapshot(
                    trace_buffer=context.trace_buffer,
                    status=context.execution_status,
                )
                performance_snapshot["audit_completed"] = audit_completed
                if context.shared_state is not None:
                    context.shared_state["execution_performance"] = performance_snapshot
                logger.info(
                    "[AgentPerformance] trace_id=%s metrics=%s",
                    context.trace_id,
                    performance_snapshot,
                )
