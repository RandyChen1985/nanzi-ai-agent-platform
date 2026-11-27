"""AgentScopeResumeHandler: 负责 AgentScope 运行时的工具权限确认与外部执行恢复流调度。"""
from __future__ import annotations

import asyncio
import json
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


def _tool_input_from_snapshot(pending: Any) -> Any:
    """从挂起快照还原工具参数，供一次性授权计算指纹。

    快照里存的是模型原始输出的 JSON 字符串，运行时 `check_permissions` 拿到的
    却是解析后的 dict。两边必须还原成同一形状，否则令牌永远命不中——用户点了
    「允许这一次」，子智能体续聊时却又弹一次卡。
    """
    snapshot = getattr(pending, "snapshot", None)
    tool_call = getattr(snapshot, "tool_call", None) or {}
    if not isinstance(tool_call, dict):
        return {}
    raw = tool_call.get("input")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"input": raw}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {}


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

                # 确认真正持有会话锁且快照有效后，再回调清理/开启运行日志，避免并发请求互相清日志。
                if on_lane_acquired is not None:
                    await on_lane_acquired()

                # 子智能体发起的确认先分流：授权要落到「父会话 + 子智能体」作用域，
                # 不能走下面的 `_grant_session_permission`——那个用的是快照里的会话
                # （子智能体的内部子会话），写进去是一条永远不会被查询到的脏记录。
                delegation = dict(getattr(pending.snapshot, "delegation", None) or {})
                if not is_external and delegation:
                    async for chunk in cls._handoff_subagent_permission(
                        pending,
                        request_id=request_id,
                        delegation=delegation,
                        confirmed=confirmed,
                        grant_scope=grant_scope,
                        user_id=lane_user_id,
                    ):
                        yield chunk
                    return

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

                reused_in_process_runner = getattr(pending, "runner", None) is not None
                runner = cls.build_agentscope_runner_from_pending(pending, user_info=user_info)
                await cls.restore_runner_execution_context(
                    agent_service,
                    runner,
                    pending,
                    user_info=user_info,
                )
                # 同进程恢复复用了原 runner，其 trace_buffer 保留了挂起轮已落库的前半段步骤：
                # 记下基线长度，收尾时只把恢复阶段新增切片写入 trace 表（Token 仍按全量聚合）；
                # 跨进程恢复新建了空 trace_buffer，只含后半段，需在 upsert 历史时累加前序 Token
                # 并顺延 trace step_number。耗时在两种模式下都是本恢复阶段的增量，统一累加。
                prior_trace_count = (
                    len(getattr(runner, "trace_buffer", None) or [])
                    if reused_in_process_runner
                    else 0
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
                    final_content: str,
                    final_timeline: Optional[List[Dict[str, Any]]],
                    final_reasoning: Optional[str],
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
                            content=final_content,
                            trace_id=pending.trace_id,
                            agent_name=handled_by,
                            agent_type=_public_agent_type(agent_config),
                            agent_display_name=(
                                getattr(agent_config, "agent_display_name", None) or handled_by
                            ),
                            prompt_tokens=p_tokens,
                            completion_tokens=c_tokens,
                            total_tokens=t_tokens,
                            reasoning_content=final_reasoning or None,
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
                    incremental_traces = (
                        list(trace_buffer[prior_trace_count:])
                        if (reused_in_process_runner and trace_buffer)
                        else trace_buffer
                    )
                    audit_coro = AuditManager.log_transaction(
                        pending.trace_id,
                        agent_config,
                        user_query,
                        final_content,
                        user_info,
                        status_value,
                        duration,
                        trace_buffer,
                        conversation_id=conversation_id,
                        reasoning_content=final_reasoning or None,
                        process_timeline=final_timeline,
                        trace_logs_to_save=incremental_traces,
                        accumulate_tokens=not reused_in_process_runner,
                        accumulate_duration=True,
                        dedup_trace_logs=True,
                        offset_trace_steps=not reused_in_process_runner,
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
                    final_reasoning = full_reasoning_content or None
                    final_content = full_response_content
                    if not cancelling:
                        final_timeline, final_reasoning, final_content = (
                            await cls._merge_prior_turn_snapshot(
                                user_id=persist_user_id,
                                conversation_id=conversation_id,
                                trace_id=pending.trace_id,
                                current_timeline=final_timeline,
                                current_reasoning=final_reasoning,
                                current_content=final_content,
                            )
                        )
                    should_persist = bool(
                        conversation_id
                        and _should_persist_turn_history(
                            final_content,
                            final_timeline,
                            final_reasoning or "",
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
                        final_content=final_content,
                        final_timeline=final_timeline,
                        final_reasoning=final_reasoning,
                        should_persist=should_persist,
                        p_tokens=0,
                        c_tokens=0,
                        t_tokens=0,
                        trace_buffer=getattr(runner, "trace_buffer", None),
                        cancelling=cancelling,
                    )
                    raise

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
                (
                    final_process_timeline,
                    merged_reasoning_content,
                    merged_response_content,
                ) = await cls._merge_prior_turn_snapshot(
                    user_id=persist_user_id,
                    conversation_id=conversation_id,
                    trace_id=pending.trace_id,
                    current_timeline=final_process_timeline,
                    current_reasoning=full_reasoning_content or None,
                    current_content=full_response_content,
                )
                # 仅在恢复轮以 success 收尾、且合并挂起前半段后的完整正文仍为空时兜底，
                # 与主执行流 ExecutionStep:260 保持同一开关与文案，绝不误伤 rejected / 二次挂起。
                if execution_status == "success" and not (merged_response_content or "").strip():
                    fallback_fn = getattr(agent_service, "_maybe_empty_response_fallback", None)
                    if callable(fallback_fn):
                        fallback_text = await fallback_fn()
                        if fallback_text:
                            merged_response_content = fallback_text
                            yield {"content": fallback_text, "status": "success"}
                should_persist_history = bool(
                    conversation_id
                    and _should_persist_turn_history(
                        merged_response_content,
                        final_process_timeline,
                        merged_reasoning_content or "",
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
                    final_content=merged_response_content,
                    final_timeline=final_process_timeline,
                    final_reasoning=merged_reasoning_content,
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
    async def _merge_prior_turn_snapshot(
        cls,
        *,
        user_id: Any,
        conversation_id: Optional[str],
        trace_id: Optional[str],
        current_timeline: Optional[List[Dict[str, Any]]],
        current_reasoning: Optional[str],
        current_content: str = "",
    ) -> tuple[Optional[List[Dict[str, Any]]], Optional[str], str]:
        """落库前回捞同一 `trace_id` 挂起前已写入会话历史的时间线、思考正文与回答正文并合并。

        挂起轮（`awaiting_permission` / `awaiting_external_execution`）已把前半段过程明细、
        思考正文与已输出正文写入 Redis 会话历史，但审计表落库延后至恢复轮；若恢复轮直接用
        仅含后半段的快照写审计表，前端终态同步与历史回放会把审批前步骤及前半段正文整体
        抹掉。这里按顺序归并最近窗口内同 `trace_id` 的全部前序 assistant 快照（兼容单轮内
        多次审批），任何读取异常仅告警并回退当前阶段快照，绝不阻断落库。
        """
        if not user_id or not conversation_id or not trace_id:
            return current_timeline, current_reasoning, current_content
        try:
            from app.services.ai.memory_service import memory_service
            from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
                merge_process_timeline_sequence,
                merge_reasoning_contents,
                merge_text_contents,
            )

            # 同 trace 挂起消息必在会话尾部，限定窗口为 20 条即可覆盖单轮连续多次审批，
            # 避免长会话每次恢复都全量反序列化 100 条历史消息。
            history = await memory_service.get_history(
                str(user_id),
                conversation_id,
                limit=20,
            )
        except Exception as exc:  # noqa: BLE001 - 回捞失败不得阻断落库与审计
            logger.warning(
                "[AgentScopeResume] 回捞同 trace 前序时间线失败 conversation=%s trace_id=%s: %s",
                conversation_id,
                trace_id,
                exc,
            )
            return current_timeline, current_reasoning, current_content

        target_trace = str(trace_id)
        prior_timelines: List[List[Dict[str, Any]]] = []
        prior_reasonings: List[str] = []
        prior_contents: List[str] = []
        for entry in history or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("role") != "assistant":
                continue
            if str(entry.get("trace_id") or "") != target_trace:
                continue
            timeline = entry.get("process_timeline")
            if isinstance(timeline, list) and timeline:
                prior_timelines.append(timeline)
            reasoning = entry.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                prior_reasonings.append(reasoning)
            content = entry.get("content")
            if isinstance(content, str) and content.strip():
                prior_contents.append(content)

        merged_timeline = (
            merge_process_timeline_sequence([*prior_timelines, current_timeline])
            if prior_timelines
            else current_timeline
        )
        merged_reasoning = (
            merge_reasoning_contents([*prior_reasonings, current_reasoning])
            if prior_reasonings
            else current_reasoning
        )
        merged_content = (
            merge_text_contents([*prior_contents, current_content])
            if prior_contents
            else current_content
        )
        return merged_timeline, merged_reasoning, merged_content

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
            restored_urls = _restore_published_download_urls_from_pending(pending)
            if restored_urls:
                context.published_download_urls = restored_urls

    @classmethod
    async def _handoff_subagent_permission(
        cls,
        pending: Any,
        *,
        request_id: str,
        delegation: Dict[str, Any],
        confirmed: bool,
        grant_scope: str,
        user_id: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """子智能体的权限确认：写授权，再把「续聊子智能体」的指令交回主会话。

        为什么确认后不直接 resume 子智能体：挂起快照属于内部子会话
        （`child_session_*`），直接恢复只会在那个内部会话里跑完——用户在主会话里
        既看不到回答，父专家也没有汇总的机会。授权写进「父会话 + 子智能体」作用域
        后，由父会话自己用 `sub_agent_call(resume=True)` 续聊；子智能体重跑到同一个
        调用时命中授权（一次性令牌按工具名 + 参数指纹精确匹配，会话授权按工具名）。
        """
        from app.services.ai.runtime.agentscope.permission_directives import (
            SUBAGENT_PERMISSION_DENY_PREFIX,
            SUBAGENT_PERMISSION_GRANT_PREFIX,
        )
        from app.services.ai.runtime.agentscope.session_permission_grants import (
            delegation_grant_scope,
            once_permission_grants,
            session_permission_grants,
            tool_args_fingerprint,
        )

        parent_conversation_id = str(delegation.get("parent_conversation_id") or "")
        agent_name = str(
            delegation.get("agent_name") or getattr(pending.snapshot, "agent_name", "") or ""
        )
        tool_name = str(getattr(pending.tool_call, "name", "") or "")
        tool_call_id = getattr(pending.tool_call, "id", None)
        scope = delegation_grant_scope(parent_conversation_id, agent_name)
        session_scope = str(grant_scope or "").strip().lower() == "session"

        # 快照的消费必须放在授权写入**之后**（见下方 pop_async 调用处）：
        # 这里的顺序决定了 grant 失败时用户还能不能重试。

        # 删除类确认带 bypass_immune，会话授权对它无效。用户点「本会话总是允许」时
        # 若照写会话授权，只会留下一条永远不生效的记录，下次照样弹——所以这类一律
        # 退化成一次性令牌（按参数指纹绑定，正好对应「就这条命令、就这一次」）。
        from app.services.ai.runtime.agentscope.tools import (
            shell_deletion_needs_confirmation,
        )

        tool_input_for_grant = _tool_input_from_snapshot(pending)
        deletion_only_once = shell_deletion_needs_confirmation(tool_name, tool_input_for_grant)
        # shell 类工具一律只给一次性令牌，与主会话路径（`_grant_session_permission`
        # 里 `supports_session_grant` 直接拒绝）保持同一口径：会话授权按工具名记忆，
        # 一个 shell 工具名下能跑的命令是无限的，写成「本会话」等于把后续所有命令
        # 一起放行。卡片本就不给这个按钮，这里再挡一次，防止客户端伪造 scope=session。
        from app.services.ai.runtime.command_policy import is_shell_like_tool_name

        effective_session = (
            session_scope and not deletion_only_once and not is_shell_like_tool_name(tool_name)
        )

        granted = False
        if confirmed and scope and tool_name:
            if effective_session:
                granted = await session_permission_grants.grant(user_id, scope, tool_name)
            else:
                granted = await once_permission_grants.grant(
                    user_id,
                    scope,
                    tool_name,
                    tool_args_fingerprint(tool_input_for_grant),
                )

        if confirmed and not granted:
            # 授权没落库还让父专家续聊，子智能体会在同一个调用上再被拦一次，
            # 用户看到的是一张卡反复弹。这里如实告诉模型「授权没成功」。
            yield {
                "type": "error",
                "status": "error",
                "content": "授权记录未能写入（可能是存储不可用），请重试或改用「自动批准」模式重发。",
                "permission_request_id": request_id,
            }
            yield {
                "type": "run_status",
                "status": "error",
                "trace_id": str(getattr(pending, "trace_id", "") or ""),
            }
            return

        # 授权已经落库（或用户明确拒绝）才消费快照。反过来的话，grant 失败时快照
        # 已被 pop，模型收到「请重试」而用户再点按钮只会拿到「请求不存在」——
        # 这一轮彻底无法继续，只能让用户重发整条消息。
        #
        # 连点确认仍然安全：两种 grant 都是按 key 覆盖写（幂等），重复写只是把
        # 同一个 key 的 TTL 刷新一次；快照也只会在成功路径上被 pop 一次。
        await pending_agentscope_confirmations.pop_async(request_id, user_id=user_id)

        yield {
            "type": "permission_result",
            "status": "success" if confirmed else "rejected",
            "permission_request_id": request_id,
            "tool_call_id": tool_call_id,
            "session_grant": (
                {
                    "granted": True,
                    "tool_name": tool_name,
                    "conversation_id": scope,
                }
                if confirmed and effective_session
                else None
            ),
        }

        resume_hint = (
            f'请用 sub_agent_call(agent_name="{agent_name}", resume=True) 让同一个子智能体'
            "接着上次的进度继续完成任务"
        )
        if confirmed:
            if deletion_only_once:
                # 别让模型以为「已获本会话允许」：删除类只认这一次，重发同一条命令
                # 还会再弹一张卡。
                grant_phrase = "仅这一次（删除类命令每次都需要确认，本会话不记忆）"
            else:
                grant_phrase = "本会话内有效" if effective_session else "仅这一次"
            directive = (
                f"{SUBAGENT_PERMISSION_GRANT_PREFIX}「{agent_name}」请求执行的工具 {tool_name} 已获允许"
                f"（{grant_phrase}）。"
                f"{resume_hint}，不要从头重跑，也不要重复申请同一授权。"
            )
        else:
            directive = (
                f"{SUBAGENT_PERMISSION_DENY_PREFIX}「{agent_name}」执行工具 {tool_name} 的请求。"
                f"{resume_hint}，并让子智能体改用不需要该工具权限的方式完成任务；"
                "不要重复申请同一授权。"
            )

        yield {
            "type": "subagent_permission_handoff",
            "status": "approved" if confirmed else "rejected",
            "permission_request_id": request_id,
            "parent_conversation_id": parent_conversation_id,
            "subagent": {
                "display_name": agent_name,
                "run_id": str(delegation.get("run_id") or ""),
            },
            "grant": {
                "scope": "session" if effective_session else "once",
                "granted": bool(granted),
                "deletion_only_once": deletion_only_once,
            },
            "directive": directive,
        }
        yield {
            "type": "run_status",
            "status": "success" if confirmed else "rejected",
            "trace_id": str(getattr(pending, "trace_id", "") or ""),
        }

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

        # 与卡片按钮同源：只读/危险/shell 类工具不写授权，避免「接口能写、按钮不给」
        # 两套口径。跨进程恢复时挂起句柄里没有工具定义（target 为 None），此时对
        # shell 类工具仍然拒绝——否则客户端可以手工构造 scope=session 把任意
        # ``mcp_sandbox_bash_*`` / ``execute_system_command`` 写进授权表，之后该会话里
        # 所有命令全程免确认（沙箱 shell 没有命令替换/敏感路径复核，粒度撑不起这个承诺）。
        target = next(
            (
                tool
                for tool in (getattr(pending, "tools", None) or [])
                if str(getattr(tool, "name", "") or "") == tool_name
            ),
            None,
        )
        if target is not None:
            grantable = supports_session_grant(target)
        else:
            from app.services.ai.runtime.command_policy import is_shell_like_tool_name

            grantable = not is_shell_like_tool_name(tool_name)
        if not grantable:
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
