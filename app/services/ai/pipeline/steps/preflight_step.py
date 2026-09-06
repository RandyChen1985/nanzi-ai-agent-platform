"""Preflight step for AgentService chat turn.

Handles:
1. Initial trace/identity chunk emission;
2. User quota pre-check;
3. Session lock check notification;
4. Input sanitization and reusable result route preparation;
5. User question card receipt validation & answer submission;
6. Initial preparation parent log & request validation timeline emission.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from app.core.orm import AsyncSessionLocal
from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.runtime.session_run_lane import conversation_run_lane
from app.services.quota_service import QuotaService

logger = logging.getLogger(__name__)


class PreflightStep(BasePipelineStep):
    """Executes preflight validations before agent orchestration."""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    async def _quota_block_message(self, user_info: Optional[Dict[str, Any]]) -> Optional[str]:
        if not user_info:
            return None
        if self.agent_service and hasattr(self.agent_service, "_quota_block_message"):
            return await self.agent_service._quota_block_message(user_info)
        async with AsyncSessionLocal() as quota_session:
            return await QuotaService(quota_session).check_before_call(user_info)

    async def check_quota_and_queue(
        self, context: PipelineContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yields init trace and checks quota before locking."""
        if context.shared_state.get("preflight_quota_checked"):
            return
        context.shared_state["preflight_quota_checked"] = True

        # 1. Initial Identity Chunk
        yield {"trace_id": context.trace_id, "status": "init"}


        # 2. Quota Check
        if context.user_info:
            quota_block = await self._quota_block_message(context.user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": context.trace_id,
                }
                context.execution_status = "quota_exceeded"
                return

        # 3. Queue wait notification if locked
        if context.conversation_id and await conversation_run_lane.is_locked(
            user_id=context.lane_user_id, conversation_id=context.conversation_id
        ):
            yield {
                "type": "log",
                "id": "session:queue_wait",
                "title": "等待上一次会话任务完成",
                "details": "检测到当前会话有未结束的任务，正在排队等待释放资源...",
                "status": "pending",
                "category": "system",
            }

    async def process_inputs_and_validation(
        self, context: PipelineContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Processes and sanitizes input messages and user questions inside the lock."""
        # 1. Message Input Sanitization
        from app.services.ai.executors.common import sanitize_client_messages_for_identity
        from app.services.ai.reusable_result import (
            CLICKED_REPLY_MARKER,
            prepare_reusable_route_input,
        )

        context.messages = sanitize_client_messages_for_identity(context.messages)
        if context.messages:
            raw_latest_user_content = str(
                context.messages[-1].get("content") or ""
            ) if isinstance(context.messages[-1], dict) else ""
            context.messages, cleaned_user_query = prepare_reusable_route_input(
                context.messages,
                raw_latest_user_content,
            )
            if CLICKED_REPLY_MARKER.lower() in raw_latest_user_content.lower():
                context.shared_state["clicked_reusable_reply"] = True

        # 2. User Question Receipt Validation
        incoming_user_message = context.messages[-1] if context.messages else None
        incoming_content = (
            incoming_user_message.get("content")
            if isinstance(incoming_user_message, dict)
            else None
        )
        from app.services.ai.user_question import (
            is_user_question_receipt_message,
            metadata_dataset_ids_from_user_question_record,
            parse_user_question_receipt,
        )

        if is_user_question_receipt_message(incoming_content):
            receipt = parse_user_question_receipt(incoming_content)
            if not receipt or not context.conversation_id:
                yield {
                    "type": "error",
                    "status": "error",
                    "content": "用户回答格式无效或当前会话无法恢复问题，请重新发起问题。",
                    "trace_id": context.trace_id,
                }
                context.execution_status = "error"
                return
            from app.services.ai.user_question_store import UserQuestionStore

            try:
                question_store = await UserQuestionStore.from_runtime()
                submitted_question = await question_store.submit_answer(
                    user_id=context.lane_user_id,
                    conversation_id=context.conversation_id,
                    question_id=receipt["question_id"],
                    selected_option_ids=receipt["selected_option_ids"],
                    custom_input=receipt["custom_input"],
                    cancelled=receipt["cancelled"],
                )
                context.user_question_cancelled = bool(receipt["cancelled"])
                restored_dataset_ids = metadata_dataset_ids_from_user_question_record(
                    submitted_question
                )
                if restored_dataset_ids:
                    context.metadata_dataset_ids = restored_dataset_ids
                    context.debug_options["metadata_dataset_scope"] = {
                        "source": "user_question",
                        "request_ids": restored_dataset_ids,
                    }
            except (PermissionError, ValueError) as exc:
                yield {
                    "type": "error",
                    "status": "error",
                    "content": f"用户回答未通过校验：{exc}",
                    "trace_id": context.trace_id,
                }
                context.execution_status = "error"
                return
            except Exception:
                logger.exception("Failed to validate user-question receipt")
                yield {
                    "type": "error",
                    "status": "error",
                    "content": "当前无法验证用户回答，请稍后重试。",
                    "trace_id": context.trace_id,
                }
                context.execution_status = "error"
                return

        # 3. Preparation Parent & Validation Timeline Logs
        from app.services.ai.agent_service import (
            _build_preparation_parent_log,
            _build_request_validation_log,
            _track_process_timeline,
        )

        context.shared_state["preparation_started_at"] = asyncio.get_running_loop().time()
        preparation_parent_log = _build_preparation_parent_log(status="pending")
        _track_process_timeline(context.shared_state["process_timeline"], preparation_parent_log)
        yield preparation_parent_log

        request_validation_log = _build_request_validation_log(
            user_info=context.user_info,
            conversation_id=context.conversation_id,
            request_observability=context.request_observability,
        )
        _track_process_timeline(context.shared_state["process_timeline"], request_validation_log)
        yield request_validation_log

    async def run(
        self, context: PipelineContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in self.check_quota_and_queue(context):
            yield chunk
        if context.execution_status != "success":
            return
        async for chunk in self.process_inputs_and_validation(context):
            yield chunk
