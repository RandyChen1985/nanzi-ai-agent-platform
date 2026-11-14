"""AgentScopeResumeHandler: 负责 AgentScope 运行时的工具权限确认与外部执行恢复流调度。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional

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
        grant_scope: str = "once",
        user_info: Optional[Dict[str, Any]] = None,
        on_lane_acquired: Optional[Callable[[], Awaitable[None]]] = None,
        credential_secret_id: str | None = None,
        scheduled_task_id: int | None = None,
        remember_credential: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in cls._run_resume_flow(
            agent_service,
            flow="permission",
            request_id=permission_request_id,
            confirmed=confirmed,
            grant_scope=grant_scope,
            results=None,
            user_info=user_info,
            on_lane_acquired=on_lane_acquired,
            credential_secret_id=credential_secret_id,
            scheduled_task_id=scheduled_task_id,
            remember_credential=remember_credential,
        ):
            yield chunk

    @classmethod
    async def resume_external_execution_stream(
        cls,
        agent_service: Any,
        *,
        external_execution_request_id: str,
        results: List[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]] = None,
        on_lane_acquired: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in cls._run_resume_flow(
            agent_service,
            flow="external",
            request_id=external_execution_request_id,
            confirmed=True,
            grant_scope="once",
            results=results,
            user_info=user_info,
            on_lane_acquired=on_lane_acquired,
        ):
            yield chunk

    @classmethod
    async def _run_resume_flow(
        cls,
        agent_service: Any,
        *,
        flow: str,
        request_id: str,
        confirmed: bool,
        grant_scope: str,
        results: Optional[List[Dict[str, Any]]],
        user_info: Optional[Dict[str, Any]] = None,
        on_lane_acquired: Optional[Callable[[], Awaitable[None]]] = None,
        credential_secret_id: str | None = None,
        scheduled_task_id: int | None = None,
        remember_credential: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.core.cancellation import await_unless_cancelling, current_task_cancelling
        from app.services.ai.agent_service import (
            _accumulate_reasoning_content,
            _accumulate_stream_content,
            _apply_turn_status_signal,
            _enrich_terminal_error_chunk,
            _filter_current_turn_download_urls,
            _final_process_timeline,
            _finalize_todo_terminal,
            _persist_assistant_message_and_summary,
            _public_agent_type,
            _restore_todo_snapshot_from_pending,
            _should_persist_turn_history,
            _track_process_timeline,
        )

        is_external = flow == "external"
        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.peek_async(
            request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": (
                    "外部执行请求不存在或已过期，请重新发起本轮对话。"
                    if is_external
                    else "工具确认请求不存在或已过期，请重新发起本轮对话。"
                ),
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": (
                    "当前用户无权提交该外部执行结果。"
                    if is_external
                    else "当前用户无权确认该工具调用。"
                ),
            }
            return

        if is_external and pending.snapshot.kind != "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求不是外部执行挂起，请使用 permission confirm 接口。",
            }
            return
        if not is_external and pending.snapshot.kind == "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求为外部执行挂起，请使用 external execution 恢复接口。",
            }
            return

        if (is_external or confirmed) and user_info:
            quota_block = await agent_service._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner_conv_id = getattr(getattr(pending, "runner", None), "conversation_id", None)
        conversation_id = runner_conv_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            # 严格先抢占会话运行锁，再注册可取消的运行句柄，防止排队请求提前覆盖活跃句柄。
            async with conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ), track_conversation_run(lane_user_id, conversation_id) as run_handle:
                # 进锁后先只读复核快照，确认未被等锁期间的其他请求消费后再执行任何副作用。
                still_pending = await pending_agentscope_confirmations.peek_async(
                    request_id,
                    user_id=current_user_id,
                )
                if still_pending is None:
                    yield {
                        "type": "error",
                        "status": "error",
                        "content": (
                            "该外部执行请求已被另一请求处理，请重新发起或稍后重试。"
                            if is_external
                            else "该工具确认已被另一请求处理，请重新发起或稍后重试。"
                        ),
                    }
                    return
                pending = still_pending

                from app.services.secrets.credential_approval import (
                    approval_for_pending, current_credential_approval, pending_ref,
                )

                ref = pending_ref(pending) if not is_external else None
                approval = None
                if ref:
                    if ref.get("scheduled_task_id") != scheduled_task_id:
                        yield {"type": "error", "status": "error", "content": "请在所属任务的凭据授权入口继续"}
                        return
                    grant_scope = "once"
                    if not current_user_id or str(pending.user_id) != str(current_user_id):
                        yield {"type": "error", "status": "error", "content": "当前用户无权确认该凭据调用"}
                        return
                    if confirmed:
                        if not credential_secret_id:
                            yield {"type": "error", "status": "error", "content": "请选择本次调用使用的凭据"}
                            return
                        from sqlalchemy.exc import SQLAlchemyError

                        from app.core.orm import AsyncSessionLocal
                        from app.services.secrets.mcp_profiles import CredentialInvocationError, pending_candidates
                        from app.services.secrets.type_catalog import CredentialTypeError

                        try:
                            async with AsyncSessionLocal() as db:
                                allowed = await pending_candidates(db, ref, str(current_user_id), secret_id=credential_secret_id)
                            if len(allowed) != 1:
                                raise CredentialInvocationError("CREDENTIAL_SELECTION_INVALID", "所选凭据已失效或授权已撤销，请重新选择")
                            approval = approval_for_pending(
                                pending, credential_secret_id, remember_credential=scheduled_task_id is not None and remember_credential,
                            )
                        except (CredentialInvocationError, CredentialTypeError) as exc:
                            yield {"type": "error", "status": "error", "code": exc.code, "content": str(exc)}
                            return
                        except SQLAlchemyError:
                            yield {
                                "type": "error", "status": "error", "code": "CREDENTIAL_QUERY_UNAVAILABLE",
                                "content": "暂时无法查询凭据，请重试",
                            }
                            return
                        except (KeyError, ValueError, TypeError):
                            yield {
                                "type": "error", "status": "error", "code": "CREDENTIAL_PROFILE_CHANGED",
                                "content": "凭据授权请求已变化，请重新发起调用",
                            }
                            return

                # 确认真正持有会话锁且快照有效后，再回调清理/开启运行日志，避免并发请求互相清日志。
                if on_lane_acquired is not None:
                    await on_lane_acquired()

                session_grant: Optional[Dict[str, Any]] = None
                if not is_external and confirmed and grant_scope == "session":
                    session_grant = await cls._grant_session_permission(
                        pending,
                        user_id=lane_user_id,
                    )

                process_timeline_state: List[Dict[str, Any]] = []
                _restore_todo_snapshot_from_pending(process_timeline_state, pending)
                if is_external:
                    initial_chunk: Dict[str, Any] = {
                        "type": "external_execution_result",
                        "status": "success",
                        "external_execution_request_id": request_id,
                        "tool_call_id": getattr(pending.tool_call, "id", None),
                    }
                else:
                    initial_chunk = {
                        "type": "permission_result",
                        "status": "success" if confirmed else "rejected",
                        "permission_request_id": request_id,
                        "tool_call_id": getattr(pending.tool_call, "id", None),
                    }
                    if session_grant is not None:
                        initial_chunk["session_grant"] = session_grant

                _track_process_timeline(process_timeline_state, initial_chunk)
                # 持锁复核后、重建重型 runner 前先发回执，避免首字节在装配工具与上下文阶段干等。
                yield initial_chunk

                runner = cls.build_agentscope_runner_from_pending(pending, user_info=user_info)
                await cls.restore_runner_execution_context(
                    agent_service,
                    runner,
                    pending,
                    user_info=user_info,
                )
                execution_results = (
                    cls.build_external_execution_results(results or [])
                    if is_external
                    else None
                )

                full_response_content = ""
                full_reasoning_content = ""
                execution_status = "success" if (is_external or confirmed) else "rejected"
                start_time = asyncio.get_running_loop().time()
                conversation_id = runner.conversation_id or conversation_id
                agent_config = getattr(runner, "config", None)
                user_query = (pending.state or {}).get("user_query") or ""
                persist_user_id = current_user_id or pending.user_id

                async def _finalize_persisted_turn(
                    *,
                    status_value: str,
                    final_timeline: Optional[List[Dict[str, Any]]],
                    should_persist: bool,
                    p_tokens: int,
                    c_tokens: int,
                    t_tokens: int,
                    trace_buffer: Any,
                    cancelling: bool,
                ) -> Optional[bool]:
                    if should_persist:
                        handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
                        resolve_tool_run_text = getattr(runner, "resolve_tool_run_text", None)
                        tool_run_text = (
                            resolve_tool_run_text() or None
                            if callable(resolve_tool_run_text)
                            else None
                        )
                        persist_coro = _persist_assistant_message_and_summary(
                            user_id=persist_user_id,
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
                            process_timeline=final_timeline,
                            tool_run_text=tool_run_text,
                            merge_summary=status_value == "success",
                            defer_summary=True,
                            status=status_value,
                        )
                        if cancelling:
                            # 传工厂而不是协程对象：await_unless_cancelling 内部是
                            # spawn_detached(factory())，直接给协程会 'coroutine' object
                            # is not callable，取消轮的落库会被整个吞掉。
                            await await_unless_cancelling(
                                lambda: persist_coro,
                                name=f"resume-persist-{pending.trace_id}",
                            )
                        else:
                            await persist_coro

                    duration = (asyncio.get_running_loop().time() - start_time) * 1000
                    audit_coro = AuditManager.log_transaction(
                        pending.trace_id,
                        agent_config,
                        user_query,
                        full_response_content,
                        user_info,
                        status_value,
                        duration,
                        trace_buffer,
                        conversation_id=conversation_id,
                        reasoning_content=full_reasoning_content or None,
                        process_timeline=final_timeline,
                    )
                    if cancelling:
                        # 同上传工厂；返回的是 Optional[bool]，不能当二元组解包。
                        # detach 成功时返回 None（本任务已取消，结果不可知），
                        # 调用方只在拿到明确的 False 时才发 history_persist_failed。
                        return await await_unless_cancelling(
                            lambda: audit_coro,
                            name=f"resume-audit-{pending.trace_id}",
                        )
                    return await audit_coro

                approval_scope = current_credential_approval.set(approval)
                try:
                    resume_stream = (
                        runner.resume_agentscope_external_execution(
                            pending,
                            execution_results=execution_results,
                        )
                        if is_external
                        else runner.resume_agentscope_native_confirmation(
                            pending,
                            confirmed=confirmed,
                        )
                    )
                    async for chunk in resume_stream:
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
                        if is_external or confirmed:
                            execution_status = _apply_turn_status_signal(execution_status, chunk)
                        yield chunk
                except asyncio.CancelledError:
                    # 被中断/取消时保留挂起快照（不 pop），用户仍可在断线/取消后检查或重试。
                    cancelling = current_task_cancelling()
                    final_timeline = _final_process_timeline(process_timeline_state)
                    should_persist = bool(
                        conversation_id
                        and _should_persist_turn_history(
                            full_response_content,
                            final_timeline,
                            full_reasoning_content,
                        )
                    )
                    if not cancelling:
                        yield {
                            "type": "run_status",
                            "status": "cancelled",
                            "trace_id": pending.trace_id,
                            "persisting": should_persist,
                        }
                    await _finalize_persisted_turn(
                        status_value="cancelled",
                        final_timeline=final_timeline,
                        should_persist=should_persist,
                        p_tokens=0,
                        c_tokens=0,
                        t_tokens=0,
                        trace_buffer=getattr(runner, "trace_buffer", None),
                        cancelling=cancelling,
                    )
                    raise
                finally:
                    current_credential_approval.reset(approval_scope)

                # 本轮已完整跑完：此刻才消费快照（仍在锁内、早于终态帧与落库），中途断开不会提前删除。
                consumed = await pending_agentscope_confirmations.pop_async(
                    request_id,
                    user_id=current_user_id,
                )
                if consumed is None:
                    logger.warning(
                        "[AgentScopeResume] 恢复轮跑完后挂起快照已不存在（过期或被消费）: request_id=%s",
                        request_id,
                    )

                guarded_response_content = _filter_current_turn_download_urls(
                    full_response_content
                )
                if guarded_response_content != full_response_content:
                    full_response_content = guarded_response_content
                    yield {
                        "type": "retraction",
                        "content": full_response_content,
                    }

                todo_completion = _finalize_todo_terminal(
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
                    logger.warning(
                        f"Failed to aggregate tokens after {flow} resume: {agg_err}"
                    )

                if p_tokens or c_tokens:
                    yield {
                        "type": "meta",
                        "prompt_tokens": p_tokens,
                        "completion_tokens": c_tokens,
                        "total_tokens": t_tokens,
                    }

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
                # 落库与审计留在同一把会话锁内完成，防止下一轮并发消息在历史尚未写入前读库或清日志。
                audit_persisted = await _finalize_persisted_turn(
                    status_value=execution_status,
                    final_timeline=final_process_timeline,
                    should_persist=should_persist_history,
                    p_tokens=p_tokens,
                    c_tokens=c_tokens,
                    t_tokens=t_tokens,
                    trace_buffer=trace_buffer,
                    cancelling=False,
                )
                if audit_persisted is False:
                    logger.error(
                        "[Audit] 恢复轮历史未落库，刷新后将查不到该轮 trace_id=%s status=%s",
                        pending.trace_id,
                        execution_status,
                    )
                    yield {
                        "type": "history_persist_failed",
                        "trace_id": pending.trace_id,
                        "conversation_id": conversation_id,
                        "message": "本轮对话历史保存失败，刷新后可能看不到这条记录",
                    }
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

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
                execution_options=dict(getattr(runner, "execution_options", {}) or {}),
            )
            runtime_context_metadata = await agent_service._runtime_context_metadata(
                runtime_model_info
            )

            await AgentContextManager.setup_context(
                config=runner.config,
                execution_options=dict(getattr(runner, "execution_options", {}) or {}),
                user_info=effective_user_info,
                credential_user_query=str((pending.state or {}).get("user_query") or ""),
                api_key=effective_user_info.get("api_key"),
                conversation_id=(
                    getattr(runner, "conversation_id", None)
                    or pending.snapshot.conversation_id
                ),
                trace_buffer=getattr(runner, "trace_buffer", None) or [],
                runtime_model_info=runtime_context_metadata,
                published_download_urls=_restore_published_download_urls_from_pending(pending),
                agent_max_toolcall_timeout_seconds=(
                    dict(getattr(runner, "execution_options", {}) or {}).get(
                        "_agent_max_toolcall_timeout_seconds"
                    )
                ),
            )
            return
        if hasattr(runner, "_ensure_agent_context"):
            context = runner._ensure_agent_context()
            context.credential_user_query = str((pending.state or {}).get("user_query") or "")
            restored_urls = _restore_published_download_urls_from_pending(pending)
            if restored_urls:
                context.published_download_urls = restored_urls

    @classmethod
    async def _grant_session_permission(
        cls,
        pending: Any,
        *,
        user_id: Any,
    ) -> Dict[str, Any]:
        """写入「本会话总是允许」记录。

        会话身份取自服务端挂起快照，不接受请求体传入的会话 ID——否则用户可以
        把授权写到任意会话上。
        """

        from app.services.ai.runtime.agentscope.session_permission_grants import (
            session_permission_grants,
        )
        from app.services.ai.runtime.agentscope.tools import supports_session_grant

        tool_name = str(getattr(pending.tool_call, "name", "") or "").strip()
        conversation_id = str(pending.snapshot.conversation_id or "").strip()

        # 与卡片按钮同源：只读/危险工具不写授权，避免「接口能写、按钮不给」两套口径。
        # 跨进程恢复时挂起句柄里没有工具定义，此时放行——真正的降级仍受 tools.py 里
        # 先于判定返回的 DENY 顺序约束，写进去也只是一条永远不会被用到的记录。
        target = next(
            (
                tool
                for tool in (getattr(pending, "tools", None) or [])
                if str(getattr(tool, "name", "") or "") == tool_name
            ),
            None,
        )
        if target is not None and not supports_session_grant(target):
            return {
                "granted": False,
                "tool_name": tool_name,
                "conversation_id": conversation_id,
                "reason": "not_grantable",
            }

        granted = await session_permission_grants.grant(
            user_id,
            conversation_id,
            tool_name,
        )
        return {
            "granted": granted,
            "tool_name": tool_name,
            "conversation_id": conversation_id,
        }

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
