"""
RouteStep: 负责流式调用生命周期的意图识别、@mention 显式路由判定、智能体专家绑定、
会话 MCP 工具挂载、模型信息解析与直通模型问答拦截。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import asyncio
import logging
import re

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.agent_service import (
    _build_model_config_log,
    _build_preparation_parent_log,
    _public_agent_type,
    looks_like_current_model_query,
    build_current_model_answer,
)
from app.services.ai.quick_result_context import normalize_quick_result_context
from app.services.ai.session_mcp_tools import apply_session_mcp_tools_to_agent_config
from app.services.ai.reusable_result import (
    ReusableResultDecision,
    prepare_reusable_route_input,
    quick_result_reuse_decision,
    should_attempt_reusable_reuse,
    CLICKED_REPLY_MARKER,
)
from app.services.ai.memory_service import memory_service
from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)


class RouteStep(BasePipelineStep):
    """管道第三阶段：意图识别、智能体专家绑定与模型参数配置解析"""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        shared_state = context.shared_state
        messages = context.messages
        user_query = str(context.user_query or shared_state.get("user_query") or "").strip()
        agent_id = context.agent_id
        agent_name = context.agent_name or shared_state.get("agent_name")
        version_id = context.version_id or shared_state.get("version_id")
        conversation_id = context.conversation_id
        user_info = context.user_info
        debug_options = context.debug_options or {}
        trace_id = context.trace_id
        trace_buffer = context.trace_buffer
        enable_multi_agent = context.enable_multi_agent

        # 1. @mention 显式路由识别
        if user_query and not (agent_id or agent_name):
            mention_match = re.match(r"^[@＠]([^\s]+)\s+(.*)$", user_query, re.DOTALL)
            if mention_match:
                agent_name = mention_match.group(1)
                user_query = mention_match.group(2).strip()
                if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
                    messages[-1]["content"] = user_query
                context.agent_name = agent_name
                context.user_query = user_query
                shared_state["agent_name"] = agent_name
                shared_state["user_query"] = user_query
                logger.info(f"Intercepted explicit @mention, routing directly to agent: {agent_name}")

        if not messages:
            yield {"content": AgentServicePrompts.EMPTY_REQUEST}
            context.execution_status = "empty_request"
            shared_state["execution_status"] = "empty_request"
            return

        normalized_quick_context = normalize_quick_result_context(context.quick_context)
        # 一旦命中 fresh-data 快捷上下文（normalize_quick_result_context 强制 requires_fresh_data=True），
        # 就必须以 quick_result_followup 处理：跳过历史 reusable-result 复用、强制 needs_fresh_data。
        # 此前额外排除「显式指定 agent/version」会让显式路由时绕过该实时契约，从而可能复用上一轮旧快照。
        quick_result_followup = bool(normalized_quick_context)

        reusable_decision_query = user_query
        if quick_result_followup:
            reusable_result_decision = quick_result_reuse_decision()
        else:
            if (shared_state or {}).get("clicked_reusable_reply"):
                reusable_decision_query = f"{user_query}\n{CLICKED_REPLY_MARKER}"
            if self.agent_service and hasattr(self.agent_service, "_resolve_reusable_result_decision"):
                reusable_result_decision = await self.agent_service._resolve_reusable_result_decision(
                    user_info=user_info,
                    conversation_id=conversation_id,
                    user_query=reusable_decision_query,
                    preferred_result_id=context.reusable_result_id,
                )
            else:
                reusable_result_decision = ReusableResultDecision(mode="none")

        route_messages, route_user_query = prepare_reusable_route_input(messages, user_query)

        from app.services.ai.reusable_result import build_reusable_result_status_event

        # 2. 异步路由解析
        agent_config = None
        route_details = None
        if self.agent_service and hasattr(self.agent_service, "_start_route_resolution"):
            route_events: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
            resolve_task = self.agent_service._start_route_resolution(
                route_events=route_events,
                resolve_kwargs={
                    "messages": route_messages,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "version_id": version_id,
                    "enable_multi_agent": enable_multi_agent,
                    "user_info": user_info,
                    "trace_buffer": trace_buffer,
                    "user_query": route_user_query,
                    "quick_result_followup": quick_result_followup,
                    "conversation_id": conversation_id,
                },
            )
            try:
                while True:
                    if not route_events.empty():
                        yield await route_events.get()
                        continue
                    if resolve_task.done():
                        break
                    route_event_task = asyncio.create_task(route_events.get())
                    try:
                        done, _ = await asyncio.wait(
                            (resolve_task, route_event_task),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if route_event_task in done:
                            yield route_event_task.result()
                    finally:
                        if not route_event_task.done():
                            route_event_task.cancel()
                        await asyncio.gather(route_event_task, return_exceptions=True)

                while not route_events.empty():
                    yield await route_events.get()

                agent_config, route_details, route_elapsed_ms, err_msg = await resolve_task
                if context.performance_tracker is not None:
                    context.performance_tracker.mark("route_resolution")
            except asyncio.CancelledError:
                if not resolve_task.done():
                    resolve_task.cancel()
                await asyncio.gather(resolve_task, return_exceptions=True)
                raise

            if err_msg:
                preparation_started_at = (
                    shared_state.get("preparation_started_at") or context.start_time
                )
                preparation_elapsed_ms = (
                    asyncio.get_running_loop().time()
                    - preparation_started_at
                ) * 1000
                yield _build_preparation_parent_log(
                    status="error",
                    details="鉴权及上下文与能力准备失败：入口专家权限校验失败",
                    execution_time_ms=preparation_elapsed_ms,
                )
                yield {
                    "type": "error",
                    "status": "denied",
                    "content": str(err_msg),
                    "trace_id": trace_id,
                }
                context.execution_status = "denied"
                shared_state["execution_status"] = "denied"
                return
        else:
            # 外部注入或单测环境模拟
            agent_config = shared_state.get("agent_config")

        if agent_config:
            shared_state["agent_config"] = agent_config
            shared_state["route_details"] = route_details
            from app.services.ai.turn_decision import TurnDecision
            route_ms = route_elapsed_ms if "route_elapsed_ms" in locals() else 0.0
            direct_agent_selection = bool(agent_id or agent_name or version_id)
            if route_details is not None and isinstance(route_details, TurnDecision):
                turn_decision = route_details.model_copy(
                    update={
                        "stage_timings_ms": {
                            **route_details.stage_timings_ms,
                            "route_resolution": route_ms,
                        }
                    }
                )
            elif direct_agent_selection or agent_config:
                turn_decision = TurnDecision.for_direct_agent_selection(
                    agent_config,
                    stage_timings_ms={"route_resolution": route_ms},
                )
            else:
                turn_decision = TurnDecision(
                    route_status="failed",
                    provenance="router_failure",
                    stage_timings_ms={"route_resolution": route_ms},
                )
            if quick_result_followup:
                turn_decision = turn_decision.model_copy(
                    update={
                        "quick_result_followup": True,
                        "needs_fresh_data": True,
                        "freshness_requirement": "realtime",
                        "reference_mode": "new_query",
                    }
                )
            allowed_reusable_result_types = None
            if turn_decision.turn_kind == "knowledge":
                allowed_reusable_result_types = {"knowledge"}
            elif turn_decision.turn_kind == "data_query":
                allowed_reusable_result_types = {"data"}
            if (
                should_attempt_reusable_reuse(
                    quick_result_followup=quick_result_followup,
                    allowed_reusable_result_types=allowed_reusable_result_types,
                )
                and self.agent_service
                and hasattr(self.agent_service, "_resolve_reusable_result_decision")
            ):
                reusable_result_decision = await self.agent_service._resolve_reusable_result_decision(
                    user_info=user_info,
                    conversation_id=conversation_id,
                    user_query=reusable_decision_query,
                    preferred_result_id=context.reusable_result_id,
                    allowed_result_types=allowed_reusable_result_types,
                )

            runner_reusable_result_id = None
            if reusable_result_decision.mode != "none":
                runner_reusable_result_id = (
                    str(reusable_result_decision.result.get("result_id") or "")
                    if reusable_result_decision.result
                    else (
                        str(context.reusable_result_id or "").strip()
                        if reusable_result_decision.reason.startswith("selected_result_")
                        else ""
                    )
                ) or None
                turn_decision = turn_decision.model_copy(
                    update={
                        "reusable_result_mode": reusable_result_decision.mode,
                        "reusable_result_id": runner_reusable_result_id,
                        "reusable_result_reason": reusable_result_decision.reason,
                    }
                )

            shared_state["reusable_result_decision"] = reusable_result_decision
            if reusable_result_decision.mode == "reuse":
                if runner_reusable_result_id:
                    shared_state["reusable_result_status"] = {
                        "status": "reused",
                        "result_id": runner_reusable_result_id,
                    }
                yield build_reusable_result_status_event(
                    status="reused",
                    payload=reusable_result_decision.result,
                )
            elif reusable_result_decision.mode == "fallback":
                yield build_reusable_result_status_event(status="fallback")

            context.agent_config = agent_config
            context.turn_decision = turn_decision
            shared_state["turn_decision"] = turn_decision
        else:
            preparation_elapsed_ms = (
                asyncio.get_running_loop().time() - shared_state.get("preparation_started_at", context.start_time)
            ) * 1000
            if quick_result_followup:
                yield _build_preparation_parent_log(
                    status="success",
                    details="快捷分析需要重新查询实时数据，但当前没有可用的数据查询智能体。",
                    execution_time_ms=preparation_elapsed_ms,
                )
                yield {
                    "content": (
                        "这条快捷分析需要重新查询实时数据，但当前没有可用的数据查询智能体。"
                        "请稍后重试，或选择一个支持数据查询的智能体后再继续。"
                    ),
                    "status": "success",
                }
            else:
                yield _build_preparation_parent_log(
                    status="error",
                    details="鉴权及上下文与能力准备失败：未找到可用目标专家。",
                    execution_time_ms=preparation_elapsed_ms,
                )
                yield {"content": AgentServicePrompts.NO_AGENT_CONFIG}
            context.execution_status = "no_agent_config"
            shared_state["execution_status"] = "no_agent_config"
            return

        # 3. 挂载 Session MCP 工具
        apply_session_mcp_tools_to_agent_config(
            agent_config,
            (debug_options or {}).get("resource_scope"),
        )

        # 4. 解析运行时模型信息
        runtime_model_info = None
        synthesis_runtime_model_info = None
        if self.agent_service and hasattr(self.agent_service, "_resolve_runtime_model_info_safe"):
            runtime_model_info = await self.agent_service._resolve_runtime_model_info_safe(
                config=agent_config,
                debug_options=debug_options,
            )
            synthesis_model_name = str(getattr(agent_config, "synthesis_model_name", "") or "").strip()
            if synthesis_model_name:
                synthesis_runtime_model_info = await self.agent_service._resolve_runtime_model_info_safe(
                    config=agent_config,
                    debug_options=debug_options,
                    model_override=synthesis_model_name,
                    phase="synthesis",
                )

        import app.services.ai.agent_service as agent_service_module

        if runtime_model_info:
            shared_state["runtime_model_info"] = runtime_model_info
            shared_state["synthesis_runtime_model_info"] = synthesis_runtime_model_info
            yield _build_model_config_log(runtime_model_info, synthesis_runtime_model_info)
            if context.performance_tracker is not None:
                context.performance_tracker.mark("runtime_model_metadata")

            # 直通模型问答
            checker = getattr(agent_service_module, "looks_like_current_model_query", looks_like_current_model_query)
            if checker(user_query):
                response = build_current_model_answer(runtime_model_info)
                agent_config.model_name = runtime_model_info.configured_model
                context.full_response_content = response
                if context.performance_tracker is not None:
                    context.performance_tracker.observe_chunk({"content": response})
                yield {
                    "type": "meta",
                    "agent_name": agent_config.agent_name,
                    "agent_display_name": agent_config.agent_display_name or agent_config.agent_name,
                    "agent_type": _public_agent_type(agent_config),
                    "model": runtime_model_info.effective_model_id,
                    "runtime_model_info": runtime_model_info.public_dict(),
                }
                yield {"content": response, "status": "success"}
                if conversation_id:
                    u_id = require_user_id(user_info)
                    asyncio.create_task(
                        memory_service.add_message(
                            u_id,
                            conversation_id,
                            "assistant",
                            response,
                            trace_id=trace_id,
                            agent_name=agent_config.agent_name,
                            agent_type=_public_agent_type(agent_config),
                            agent_display_name=(agent_config.agent_display_name or agent_config.agent_name),
                        )
                    )
                context.execution_status = "answered_directly"
                shared_state["execution_status"] = "answered_directly"
                return

        if self.agent_service and hasattr(self.agent_service, "_rebuild_context_for_resolved_model"):
            messages = await self.agent_service._rebuild_context_for_resolved_model(
                messages=context.messages,
                runtime_model_info=runtime_model_info,
                conversation_id=conversation_id,
                user_info=user_info,
                agent_id=str(getattr(agent_config, "agent_id", "") or "") or None,
                agent_name=getattr(agent_config, "agent_name", None),
                version_id=version_id,
                shared_state=shared_state,
                synthesis_runtime_model_info=synthesis_runtime_model_info,
            )
            context.messages = messages
            final_context_event = (shared_state or {}).pop(
                "context_final_compaction_event", None
            )
            if final_context_event:
                if hasattr(self.agent_service, "_persist_context_compaction_event"):
                    await self.agent_service._persist_context_compaction_event(
                        final_context_event,
                        user_id=context.lane_user_id,
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                        source="platform",
                        stage="resolved_model",
                        agent_name=getattr(agent_config, "agent_name", None),
                        model_name=getattr(agent_config, "model_name", None),
                    )
                yield final_context_event
