"""AgentScopeResumeHandler: 负责 AgentScope 运行时的工具权限确认与外部执行恢复流调度。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.audit import AuditManager, aggregate_tokens_from_trace_buffer
from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
from app.services.ai.runtime.conversation_run_registry import track_conversation_run
from app.services.ai.runtime.session_run_lane import (
    ConversationRunBusyError,
    conversation_run_lane,
)

logger = logging.getLogger(__name__)


class AgentScopeResumeHandler:
    """封装 AgentScope 工具确认与外部执行挂起恢复的流式执行逻辑。"""

    @classmethod
    async def resume_permission_stream(
        cls,
        agent_service: Any,
        *,
        permission_request_id: str,
        confirmed: bool,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.agent_service import (
            _accumulate_reasoning_content,
            _accumulate_stream_content,
            _apply_turn_status_signal,
            _enrich_terminal_error_chunk,
            _filter_current_turn_download_urls,
            _final_process_timeline,
            _finalize_todo_success,
            _persist_assistant_message_and_summary,
            _public_agent_type,
            _restore_todo_snapshot_from_pending,
            _should_persist_turn_history,
            _track_process_timeline,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            permission_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "工具确认请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权确认该工具调用。",
            }
            return

        if pending.snapshot.kind == "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求为外部执行挂起，请使用 external execution 恢复接口。",
            }
            return

        if confirmed and user_info:
            quota_block = await agent_service._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = cls.build_agentscope_runner_from_pending(pending, user_info=user_info)
        await cls.restore_runner_execution_context(
            agent_service,
            runner,
            pending,
            user_info=user_info,
        )

        process_timeline_state: List[Dict[str, Any]] = []
        _restore_todo_snapshot_from_pending(process_timeline_state, pending)
        permission_chunk = {
            "type": "permission_result",
            "status": "success" if confirmed else "rejected",
            "permission_request_id": permission_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }
        _track_process_timeline(process_timeline_state, permission_chunk)
        yield permission_chunk

        full_response_content = ""
        full_reasoning_content = ""
        execution_status = "success" if confirmed else "rejected"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_native_confirmation(
                    pending,
                    confirmed=confirmed,
                ):
                    if run_handle is not None and run_handle.cancelled:
                        raise asyncio.CancelledError
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=runner.config,
                        model_name=getattr(runner.config, "model_name", None),
                    )
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    _track_process_timeline(process_timeline_state, chunk)
                    if confirmed:
                        execution_status = _apply_turn_status_signal(execution_status, chunk)
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        guarded_response_content = _filter_current_turn_download_urls(
            full_response_content
        )
        if guarded_response_content != full_response_content:
            full_response_content = guarded_response_content
            yield {
                "type": "retraction",
                "content": full_response_content,
            }

        todo_completion = _finalize_todo_success(
            process_timeline_state,
            execution_status=execution_status,
        )
        if todo_completion:
            yield todo_completion

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            p_tokens, c_tokens, t_tokens = (
                aggregate_tokens_from_trace_buffer(trace_buffer)
                if trace_buffer
                else (0, 0, 0)
            )
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after permission resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        final_process_timeline = _final_process_timeline(process_timeline_state)
        should_persist_history = bool(
            conversation_id
            and _should_persist_turn_history(
                full_response_content,
                final_process_timeline,
                full_reasoning_content,
            )
        )
        yield {
            "type": "run_status",
            "status": execution_status,
            "trace_id": pending.trace_id,
            "persisting": should_persist_history,
        }
        if should_persist_history:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            resolve_tool_run_text = getattr(runner, "resolve_tool_run_text", None)
            tool_run_text = (
                resolve_tool_run_text() or None
                if callable(resolve_tool_run_text)
                else None
            )
            await _persist_assistant_message_and_summary(
                user_id=u_id,
                conversation_id=conversation_id,
                content=full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                agent_type=_public_agent_type(agent_config),
                agent_display_name=(
                    getattr(agent_config, "agent_display_name", None) or handled_by
                ),
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
                reasoning_content=full_reasoning_content or None,
                process_timeline=final_process_timeline,
                tool_run_text=tool_run_text,
                merge_summary=execution_status == "success",
                defer_summary=True,
                status=execution_status,
            )

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        await AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
            reasoning_content=full_reasoning_content or None,
            process_timeline=_final_process_timeline(process_timeline_state),
        )

    @classmethod
    async def resume_external_execution_stream(
        cls,
        agent_service: Any,
        *,
        external_execution_request_id: str,
        results: List[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.agent_service import (
            _accumulate_reasoning_content,
            _accumulate_stream_content,
            _apply_turn_status_signal,
            _enrich_terminal_error_chunk,
            _filter_current_turn_download_urls,
            _final_process_timeline,
            _finalize_todo_success,
            _persist_assistant_message_and_summary,
            _public_agent_type,
            _restore_todo_snapshot_from_pending,
            _should_persist_turn_history,
            _track_process_timeline,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            external_execution_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "外部执行请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权提交该外部执行结果。",
            }
            return

        if pending.snapshot.kind != "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求不是外部执行挂起，请使用 permission confirm 接口。",
            }
            return

        if user_info:
            quota_block = await agent_service._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = cls.build_agentscope_runner_from_pending(pending, user_info=user_info)
        await cls.restore_runner_execution_context(
            agent_service,
            runner,
            pending,
            user_info=user_info,
        )
        execution_results = cls.build_external_execution_results(results)

        process_timeline_state: List[Dict[str, Any]] = []
        _restore_todo_snapshot_from_pending(process_timeline_state, pending)
        external_chunk = {
            "type": "external_execution_result",
            "status": "success",
            "external_execution_request_id": external_execution_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }
        _track_process_timeline(process_timeline_state, external_chunk)
        yield external_chunk

        full_response_content = ""
        full_reasoning_content = ""
        execution_status = "success"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_external_execution(
                    pending,
                    execution_results=execution_results,
                ):
                    if run_handle is not None and run_handle.cancelled:
                        raise asyncio.CancelledError
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=runner.config,
                        model_name=getattr(runner.config, "model_name", None),
                    )
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    _track_process_timeline(process_timeline_state, chunk)
                    execution_status = _apply_turn_status_signal(execution_status, chunk)
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        guarded_response_content = _filter_current_turn_download_urls(
            full_response_content
        )
        if guarded_response_content != full_response_content:
            full_response_content = guarded_response_content
            yield {
                "type": "retraction",
                "content": full_response_content,
            }

        todo_completion = _finalize_todo_success(
            process_timeline_state,
            execution_status=execution_status,
        )
        if todo_completion:
            yield todo_completion

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            p_tokens, c_tokens, t_tokens = (
                aggregate_tokens_from_trace_buffer(trace_buffer)
                if trace_buffer
                else (0, 0, 0)
            )
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after external resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        final_process_timeline = _final_process_timeline(process_timeline_state)
        should_persist_history = bool(
            conversation_id
            and _should_persist_turn_history(
                full_response_content,
                final_process_timeline,
                full_reasoning_content,
            )
        )
        yield {
            "type": "run_status",
            "status": execution_status,
            "trace_id": pending.trace_id,
            "persisting": should_persist_history,
        }
        if should_persist_history:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            resolve_tool_run_text = getattr(runner, "resolve_tool_run_text", None)
            tool_run_text = (
                resolve_tool_run_text() or None
                if callable(resolve_tool_run_text)
                else None
            )
            await _persist_assistant_message_and_summary(
                user_id=u_id,
                conversation_id=conversation_id,
                content=full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                agent_type=_public_agent_type(agent_config),
                agent_display_name=(
                    getattr(agent_config, "agent_display_name", None) or handled_by
                ),
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
                reasoning_content=full_reasoning_content or None,
                process_timeline=final_process_timeline,
                tool_run_text=tool_run_text,
                merge_summary=execution_status == "success",
                defer_summary=True,
                status=execution_status,
            )

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        await AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
            reasoning_content=full_reasoning_content or None,
            process_timeline=_final_process_timeline(process_timeline_state),
        )

    @classmethod
    async def restore_runner_execution_context(
        cls,
        agent_service: Any,
        runner: Any,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """工具确认/外部执行恢复前重建 AgentContext，避免 user_id 等会话信息丢失。"""
        from app.services.ai.agent_service import _restore_published_download_urls_from_pending

        effective_user_info = user_info or getattr(runner, "user_info", None)
        if effective_user_info and getattr(runner, "config", None) is not None:
            from app.services.ai.context_manager import AgentContextManager

            runtime_model_info = await agent_service._resolve_runtime_model_info_safe(
                config=runner.config,
                debug_options=dict(getattr(runner, "debug_options", {}) or {}),
            )
            runtime_context_metadata = await agent_service._runtime_context_metadata(
                runtime_model_info
            )

            await AgentContextManager.setup_context(
                config=runner.config,
                debug_options=dict(getattr(runner, "debug_options", {}) or {}),
                user_info=effective_user_info,
                api_key=effective_user_info.get("api_key"),
                conversation_id=(
                    getattr(runner, "conversation_id", None)
                    or pending.snapshot.conversation_id
                ),
                trace_buffer=getattr(runner, "trace_buffer", None) or [],
                runtime_model_info=runtime_context_metadata,
                published_download_urls=_restore_published_download_urls_from_pending(pending),
                agent_max_toolcall_timeout_seconds=(
                    dict(getattr(runner, "debug_options", {}) or {}).get(
                        "_agent_max_toolcall_timeout_seconds"
                    )
                ),
            )
            return
        if hasattr(runner, "_ensure_agent_context"):
            context = runner._ensure_agent_context()
            restored_urls = _restore_published_download_urls_from_pending(pending)
            if restored_urls:
                context.published_download_urls = restored_urls

    @classmethod
    def build_agentscope_runner_from_pending(
        cls,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Any:
        runner = pending.runner
        if runner is not None:
            if user_info:
                runner.user_info = {**(runner.user_info or {}), **user_info}
            return runner

        ctx = pending.snapshot.runner_context or {}
        if ctx.get("runner_type") == "data":
            from app.services.ai.runners.data_agent_runner import DataAgentRunner

            return DataAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

        if ctx.get("runner_type") in ("assistant", "general"):
            return AssistantAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        raise ValueError(f"Unsupported runner_type for resume: {ctx.get('runner_type')!r}")

    @staticmethod
    def build_external_execution_results(results: List[Dict[str, Any]]) -> List[Any]:
        from agentscope.message import ToolResultBlock, ToolResultState

        state_map = {
            "success": ToolResultState.SUCCESS,
            "error": ToolResultState.ERROR,
            "running": ToolResultState.RUNNING,
            "interrupted": ToolResultState.INTERRUPTED,
            "denied": ToolResultState.DENIED,
        }
        return [
            ToolResultBlock(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                output=str(item.get("output") or ""),
                state=state_map.get(
                    str(item.get("state") or "success").lower(),
                    ToolResultState.SUCCESS,
                ),
            )
            for item in results
        ]
