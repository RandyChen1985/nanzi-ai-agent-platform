"""
ExecutionStep: 负责流式调用生命周期的 Meta 状态派发、智能体 Executor 调度与多 Agent 协同、
流式内容/思考累积、状态机推进与空回复/工具门禁检查。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import logging

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.agent_service import (
    _enrich_terminal_error_chunk,
    _accumulate_stream_content,
    _accumulate_reasoning_content,
    _apply_turn_status_signal,
    _public_agent_type,
    _trace_has_tool_call,
)

logger = logging.getLogger(__name__)


class ExecutionStep(BasePipelineStep):
    """管道第五阶段：Runner 调度、流式执行与输出状态推进"""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        shared_state = context.shared_state

        # 若前面步骤已非 success 终态（例如已直接回答或阻断），直接跳过执行
        if context.execution_status != "success":
            return

        agent_config = shared_state.get("agent_config") or context.agent_config
        runtime_model_info = shared_state.get("runtime_model_info")
        route_details = shared_state.get("route_details")
        turn_decision = context.turn_decision or shared_state.get("turn_decision")
        if turn_decision is None and agent_config:
            from app.services.ai.turn_decision import TurnDecision
            turn_decision = TurnDecision.for_direct_agent_selection(agent_config)
            shared_state["turn_decision"] = turn_decision
        trace_id = context.trace_id
        trace_buffer = context.trace_buffer
        user_query = str(context.user_query or shared_state.get("user_query") or "")
        messages = context.messages
        debug_options = context.debug_options
        permission_options = context.permission_options
        user_info = context.user_info
        api_key = context.api_key
        conversation_id = context.conversation_id
        enable_multi_agent = context.enable_multi_agent

        # 1. 产出 meta 事件
        if agent_config:
            model_id = getattr(runtime_model_info, "effective_model_id", None) or getattr(
                agent_config, "model_name", "default"
            )
            meta_event: Dict[str, Any] = {
                "type": "meta",
                "agent_name": getattr(agent_config, "agent_name", "agent"),
                "agent_display_name": getattr(agent_config, "agent_display_name", None)
                or getattr(agent_config, "agent_name", "agent"),
                "agent_type": _public_agent_type(agent_config),
                "model": model_id,
            }
            if runtime_model_info:
                meta_event.update({
                    "configured_model": runtime_model_info.configured_model,
                    "effective_model_id": runtime_model_info.effective_model_id,
                    "model_source": runtime_model_info.source,
                    "model_resolution_status": runtime_model_info.resolution_status,
                })
            if turn_decision:
                from app.services.ai.turn_decision import (
                    default_thought_expanded,
                    turn_kind_label,
                )

                if turn_decision.turn_kind == "data_query":
                    turn_display_label = "ChatBI 请求类别分析"
                else:
                    turn_display_label = turn_kind_label(turn_decision.turn_kind)
                meta_event.update({
                    "turn_type": turn_decision.turn_kind,
                    "turn_type_label": turn_display_label,
                    "thought_expanded_default": default_thought_expanded(
                        turn_decision.turn_kind
                    ),
                    "decision_trace": turn_decision.trace_payload(
                        stage_timings_ms={"intent_resolution": 0.0},
                        executor=_public_agent_type(agent_config),
                    ),
                })
            if context.ltm_profile and context.ltm_loaded_data:
                meta_event["ltm_applied"] = True
                meta_event["ltm_data"] = context.ltm_loaded_data
            if (
                getattr(turn_decision, "turn_kind", None) == "knowledge"
                or context.knowledge_dataset_ids
                or (getattr(agent_config, "engine_config", None) or {}).get("dataset_ids")
            ):
                try:
                    from app.services.ai.knowledge_utils import build_rag_retrieval_debug_meta

                    meta_event["rag_retrieval"] = await build_rag_retrieval_debug_meta()
                except Exception as rag_meta_err:
                    logger.warning(
                        "[ExecutionStep] Failed to build rag_retrieval meta: %s",
                        rag_meta_err,
                    )
            yield meta_event

        # 2. 调度执行
        full_response_content = context.full_response_content
        full_reasoning_content = context.full_reasoning_content
        execution_status = context.execution_status
        has_data_output = bool(context.has_data_output)
        tool_run_text = context.tool_run_text

        def sync_execution_state() -> None:
            context.full_response_content = full_response_content
            context.full_reasoning_content = full_reasoning_content
            context.execution_status = execution_status
            context.has_data_output = has_data_output
            context.tool_run_text = tool_run_text
            shared_state["execution_status"] = execution_status
            shared_state["full_response_content"] = full_response_content
            shared_state["full_reasoning_content"] = full_reasoning_content

        performance_tracker = context.performance_tracker or shared_state.get("performance_tracker")

        secondary_agents = getattr(route_details, "secondary_agents", []) if route_details else []

        executor_stream = None
        if enable_multi_agent and secondary_agents and self.agent_service and hasattr(self.agent_service, "_execute_multi_agent"):
            executor_stream = self.agent_service._execute_multi_agent(
                agent_config,
                secondary_agents,
                user_query,
                messages,
                trace_id,
                trace_buffer,
                debug_options,
                permission_options,
                user_info,
                api_key,
                conversation_id,
                turn_decision,
            )
        elif self.agent_service and hasattr(self.agent_service, "_dispatch_executor"):
            executor = await self.agent_service._dispatch_executor(
                agent_config=agent_config,
                user_query=user_query,
                messages=messages,
                trace_id=trace_id,
                trace_buffer=trace_buffer,
                debug_options=debug_options,
                permission_options=permission_options,
                user_info=user_info,
                conversation_id=conversation_id,
                turn_decision=turn_decision,
            )
            if hasattr(executor, "execute"):
                executor_stream = executor.execute(messages)

            resolve_has_data_output = getattr(executor, "resolve_has_data_output", None)
            if callable(resolve_has_data_output):
                has_data_output = bool(resolve_has_data_output())

            resolve_tool_run_text = getattr(executor, "resolve_tool_run_text", None)
            if callable(resolve_tool_run_text):
                tool_run_text = resolve_tool_run_text() or None

        if executor_stream:
            if performance_tracker is not None:
                performance_tracker.mark("executor_start")
            async for chunk in executor_stream:
                if isinstance(chunk, dict):
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=agent_config,
                        model_name=getattr(agent_config, "model_name", None),
                    )
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(
                        full_reasoning_content, chunk
                    )
                    execution_status = _apply_turn_status_signal(execution_status, chunk)
                    if chunk.get("type") == "reusable_result_status":
                        status = str(chunk.get("status") or "")
                        result_id = str(chunk.get("result_id") or "").strip()
                        if status in {"saved", "reused"} and result_id:
                            existing = shared_state.get("reusable_result_status")
                            if not (
                                isinstance(existing, dict)
                                and existing.get("status") == "reused"
                                and status == "saved"
                            ):
                                shared_state["reusable_result_status"] = {
                                    "status": status,
                                    "result_id": result_id,
                                }
                    if performance_tracker is not None:
                        performance_tracker.observe_chunk(chunk)
                    sync_execution_state()
                yield chunk
            if performance_tracker is not None:
                performance_tracker.mark("executor_finish")

            if "executor" in locals():
                resolve_has_data_output = getattr(executor, "resolve_has_data_output", None)
                if callable(resolve_has_data_output):
                    has_data_output = bool(resolve_has_data_output())

                resolve_tool_run_text = getattr(executor, "resolve_tool_run_text", None)
                if callable(resolve_tool_run_text):
                    tool_run_text = resolve_tool_run_text() or None
                sync_execution_state()

        # 3. 空回复降级兜底
        if execution_status == "success" and not (full_response_content or "").strip():
            if self.agent_service and hasattr(self.agent_service, "_maybe_empty_response_fallback"):
                fallback_text = await self.agent_service._maybe_empty_response_fallback()
                if fallback_text:
                    full_response_content = fallback_text
                    sync_execution_state()
                    yield {"content": fallback_text, "status": "success"}

        # 4. 定时任务工具调用校验
        requires_tool_execution = bool(
            user_info
            and user_info.get("is_scheduled_task")
            and user_info.get("requires_tool_execution")
        )
        if (
            requires_tool_execution
            and execution_status == "success"
            and not _trace_has_tool_call(trace_buffer)
        ):
            from app.services.ai.agent_service import NO_TOOL_EXECUTION_MESSAGE

            execution_status = "no_tool_execution"
            no_tool_message = (
                f"{NO_TOOL_EXECUTION_MESSAGE}，本次只产生了模型回复，没有产生工具调用；"
                "已按未完成处理，请检查任务指令或智能体工具配置。"
            )
            full_response_content = (
                f"{full_response_content}\n\n{no_tool_message}"
                if full_response_content
                else no_tool_message
            )
            no_tool_error = await _enrich_terminal_error_chunk(
                {
                    "type": "error",
                    "status": "error",
                    "content": no_tool_message,
                },
                config=agent_config,
                model_name=getattr(agent_config, "model_name", None),
            )
            sync_execution_state()
            yield no_tool_error

        # 5. 回写状态至 context 与 shared_state
        sync_execution_state()
