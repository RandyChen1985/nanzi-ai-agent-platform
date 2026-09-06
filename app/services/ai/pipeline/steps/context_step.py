"""
ContextStep: 负责流式对话生命周期的上下文提取、服务端会话历史读取、快照合并、Token 预算窗口裁剪、
溢出压缩（Compaction）与技能元数据富化。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import logging

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.agent_service import (
    _regular_completion_history,
    _apply_context_snapshot,
    history_messages_for_llm,
    _window_for_context,
    _client_prefix_history_len,
    _build_context_history_log,
    _track_process_timeline,
)
from app.services.ai.memory_service import memory_service
from app.utils.skill_metadata import enrich_messages_with_skill_meta
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class ContextStep(BasePipelineStep):
    """管道第二阶段：会话上下文加载、滑动窗口裁剪与记忆富化"""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        messages = list(context.messages or [])
        user_msg = (
            messages[-1]
            if messages
            and isinstance(messages[-1], dict)
            and messages[-1].get("role") == "user"
            else None
        )
        context.shared_state["user_msg"] = user_msg
        user_query = str(user_msg.get("content") or "").strip() if user_msg else ""
        context.user_query = user_query
        context.shared_state["user_query"] = user_query

        conversation_id = context.conversation_id
        raw_lane_user_id = context.lane_user_id
        if raw_lane_user_id is None:
            raw_lane_user_id = (context.user_info or {}).get("user_id") or (
                context.user_info or {}
            ).get("id")
            context.lane_user_id = raw_lane_user_id
        lane_user_id = str(raw_lane_user_id or "")
        agent_id = context.agent_id
        agent_name = (
            context.agent_name
            or context.shared_state.get("agent_name")
            or getattr(context.shared_state.get("agent_config"), "name", None)
        )
        version_id = context.shared_state.get("version_id")
        trace_id = context.trace_id

        context_source_history_count = 0
        context_selected_history_count = 0
        context_window_history_count = 0
        context_trimmed_history_count = 0
        context_history_budget: Optional[int] = None
        context_max_messages: Optional[int] = None
        context_compaction_applied = False
        context_request_history_count = _client_prefix_history_len(messages)

        if conversation_id:
            u_id = lane_user_id
            raw_history = await memory_service.get_history(u_id, conversation_id)
            server_history = _regular_completion_history(raw_history, messages)
            server_history = await _apply_context_snapshot(
                server_history, user_id=u_id, conversation_id=conversation_id
            )
            context_source_history_count = len(history_messages_for_llm(server_history))

            if self.agent_service and hasattr(self.agent_service, "_resolve_pre_route_context_budget"):
                runtime_max_tokens = await self.agent_service._resolve_pre_route_context_budget()
            else:
                runtime_max_tokens = 65536

            max_context_str = await ConfigService.get("agent_max_context_messages", "60")
            try:
                max_context = int(max_context_str)
            except (ValueError, TypeError):
                max_context = 60

            if self.agent_service and hasattr(self.agent_service, "_resolve_history_context_budget"):
                history_max_tokens = await self.agent_service._resolve_history_context_budget(
                    runtime_max_tokens
                )
            else:
                history_max_tokens = max(1000, runtime_max_tokens - 4000)

            context_history_budget = history_max_tokens
            context_max_messages = max_context

            context.shared_state["context_source_history"] = list(server_history or [])
            context.shared_state["context_history_budget"] = history_max_tokens

            if user_msg and user_msg.get("role") == "user":
                await memory_service.add_message(
                    u_id,
                    conversation_id,
                    "user",
                    user_msg["content"],
                    files=user_msg.get("files"),
                )
                context.shared_state["context_user_message"] = user_msg

                window_hidden = _window_for_context(
                    server_history if server_history else [],
                    max_context,
                    history_max_tokens,
                )
                context_history = history_messages_for_llm(window_hidden)
                context_window_history_count = len(context_history)
                context_full_history = history_messages_for_llm(server_history)
                context_trimmed_history_count = max(
                    0,
                    context_source_history_count - context_window_history_count,
                )

                ctx_event: dict = {}
                if self.agent_service and hasattr(self.agent_service, "_maybe_compact_overflow"):
                    context_history = await self.agent_service._maybe_compact_overflow(
                        context_full_history,
                        context_history,
                        user_id=lane_user_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        out=ctx_event,
                        token_budget=history_max_tokens,
                        enable_llm_summary=False,
                        physical_window=runtime_max_tokens,
                    )
                if ctx_event:
                    context_compaction_applied = True
                    ctx_event = dict(ctx_event)
                    ctx_event["type"] = "context_summarized"
                    if self.agent_service and hasattr(
                        self.agent_service, "_persist_context_compaction_event"
                    ):
                        await self.agent_service._persist_context_compaction_event(
                            ctx_event,
                            user_id=lane_user_id,
                            conversation_id=conversation_id,
                            trace_id=trace_id,
                            source="platform",
                            stage="pre_route",
                            agent_name=agent_name,
                        )
                    yield ctx_event
                messages = context_history + [user_msg]
                context_selected_history_count = len(context_history)
            else:
                context.shared_state["context_user_message"] = None
                window = history_messages_for_llm(
                    _window_for_context(
                        server_history if server_history else [],
                        max_context,
                        history_max_tokens,
                    )
                )
                context_window_history_count = len(window)
                context_trimmed_history_count = max(
                    0,
                    context_source_history_count - context_window_history_count,
                )

                ctx_event = {}
                if self.agent_service and hasattr(self.agent_service, "_maybe_compact_overflow"):
                    messages = await self.agent_service._maybe_compact_overflow(
                        history_messages_for_llm(server_history),
                        window,
                        user_id=lane_user_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        out=ctx_event,
                        token_budget=history_max_tokens,
                        enable_llm_summary=False,
                        physical_window=runtime_max_tokens,
                    )
                else:
                    messages = window

                if ctx_event:
                    context_compaction_applied = True
                    ctx_event = dict(ctx_event)
                    ctx_event["type"] = "context_summarized"
                    if self.agent_service and hasattr(
                        self.agent_service, "_persist_context_compaction_event"
                    ):
                        await self.agent_service._persist_context_compaction_event(
                            ctx_event,
                            user_id=lane_user_id,
                            conversation_id=conversation_id,
                            trace_id=trace_id,
                            source="platform",
                            stage="pre_route",
                            agent_name=agent_name,
                        )
                    yield ctx_event
                context_selected_history_count = len(messages)
        else:
            context_selected_history_count = context_request_history_count

        context_history_log = _build_context_history_log(
            conversation_id=conversation_id,
            source_history_count=context_source_history_count,
            selected_history_count=context_selected_history_count,
            trimmed_history_count=context_trimmed_history_count,
            history_token_budget=context_history_budget,
            max_context_messages=context_max_messages,
            compaction_applied=context_compaction_applied,
            request_history_count=context_request_history_count,
        )
        if "process_timeline" in context.shared_state and isinstance(
            context.shared_state["process_timeline"], list
        ):
            _track_process_timeline(context.shared_state["process_timeline"], context_history_log)
        yield context_history_log

        enrich_messages_with_skill_meta(messages)
        context.messages = messages

        if context.user_question_cancelled:
            from app.services.ai.agent_service import _final_process_timeline
            import asyncio

            cancellation_message = "已取消本次提问，本次任务已停止。"
            context.full_response_content = cancellation_message
            context.execution_status = "cancelled"
            context.shared_state["execution_status"] = "cancelled"
            resolved_agent_name = "sys_question_cancel"
            resolved_display_name = "系统助手"
            if conversation_id:
                asyncio.create_task(
                    memory_service.add_message(
                        lane_user_id,
                        conversation_id,
                        "assistant",
                        cancellation_message,
                        trace_id=trace_id,
                        agent_name=resolved_agent_name,
                        agent_type="system",
                        agent_display_name=resolved_display_name,
                        process_timeline=_final_process_timeline(
                            context.shared_state.get("process_timeline")
                        ),
                    )
                )
            yield {
                "type": "meta",
                "agent_name": resolved_agent_name,
                "agent_display_name": resolved_display_name,
                "agent_type": "system",
            }
            yield {
                "content": cancellation_message,
                "status": "success",
                "trace_id": trace_id,
            }
            return
