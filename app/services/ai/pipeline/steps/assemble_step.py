"""
AssembleStep: 负责流式对话生命周期的系统提示词分层组装、能力目录日志发布、
安全与防幻觉边界注入，以及调试选项覆盖。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import asyncio
import logging

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.agent_service import (
    _build_capability_catalog_log,
    _build_prompt_assembly_log,
    _build_preparation_parent_log,
    build_chat_history_boundary_prompt,
)
from app.services.ai.prompt_assembler import (
    PromptAssemblyInput,
    assemble_system_prompt,
    resolve_prompt_assembler_flags,
)
from app.services.ai.business_context import sanitize_injected_context
from app.services.ai.agent_prompts import AgentServicePrompts
from app.core.config import settings

logger = logging.getLogger(__name__)


class AssembleStep(BasePipelineStep):
    """管道第四阶段：系统提示词分层组装、能力目录与安全边界注入"""

    def __init__(self, agent_service: Any = None):
        self.agent_service = agent_service

    @staticmethod
    def _should_forbid_quick_suggestions(user_info: Optional[Dict[str, Any]]) -> bool:
        if not user_info:
            return False

        def enabled(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        return any(
            enabled(user_info.get(key))
            for key in (
                "quick_suggestions_forbidden",
                "is_scheduled_task",
                "is_subscription_task",
            )
        )

    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        shared_state = context.shared_state
        debug_options = context.debug_options or {}
        user_info = context.user_info
        trace_id = context.trace_id

        agent_config = shared_state.get("agent_config") or getattr(context, "agent_config", None)
        preflight_ctx = shared_state.get("preflight_ctx")

        if agent_config:
            from app.services.ai.context_manager import AgentContextManager
            from app.services.ai.knowledge_utils import merge_request_knowledge_dataset_ids

            request_knowledge_dataset_ids = merge_request_knowledge_dataset_ids(
                context.knowledge_dataset_ids,
                context.messages,
            )
            engine_cfg = getattr(agent_config, "engine_config", None) or {}
            configured_agent_dataset_ids = list(
                engine_cfg.get("dataset_ids") or []
            )
            shared_state["request_knowledge_dataset_ids"] = request_knowledge_dataset_ids
            shared_state["configured_agent_dataset_ids"] = configured_agent_dataset_ids

            runtime_model_info = shared_state.get("runtime_model_info")
            runtime_context_metadata = None
            if self.agent_service and hasattr(self.agent_service, "_runtime_context_metadata"):
                runtime_context_metadata = await self.agent_service._runtime_context_metadata(
                    runtime_model_info,
                    history_budget=shared_state.get("context_history_budget"),
                    synthesis_runtime_model_info=shared_state.get("synthesis_runtime_model_info"),
                )
            elif runtime_model_info:
                runtime_context_metadata = runtime_model_info.public_dict()

            authorized_paths = []
            current_turn_paths = []
            if self.agent_service and hasattr(self.agent_service, "_authorized_attachment_paths"):
                authorized_paths = self.agent_service._authorized_attachment_paths(context.messages)
            if self.agent_service and hasattr(self.agent_service, "_current_turn_attachment_paths"):
                current_turn_paths = self.agent_service._current_turn_attachment_paths(context.messages)

            context_setup_kwargs = dict(
                config=agent_config,
                debug_options=debug_options,
                user_info=user_info,
                api_key=context.api_key,
                conversation_id=context.conversation_id,
                knowledge_dataset_ids=request_knowledge_dataset_ids,
                agent_dataset_ids=configured_agent_dataset_ids,
                metadata_dataset_ids=context.metadata_dataset_ids,
                authorized_attachment_paths=authorized_paths,
                current_turn_attachment_paths=current_turn_paths,
                trace_buffer=context.trace_buffer,
                runtime_model_info=runtime_context_metadata,
                published_download_urls=shared_state.get("published_download_urls", []),
                agent_max_toolcall_timeout_seconds=getattr(context, "agent_max_toolcall_timeout_seconds", None),
            )
            await AgentContextManager.setup_context(**context_setup_kwargs)
            if context.performance_tracker is not None:
                context.performance_tracker.mark("context_setup")

            turn_decision = context.turn_decision or shared_state.get("turn_decision")
            if turn_decision is not None and turn_decision.turn_kind == "knowledge":
                agent_config = await AgentContextManager.enrich_for_knowledge_turn(
                    agent_config,
                    user_query=str(context.user_query or shared_state.get("user_query") or ""),
                )
                context.agent_config = agent_config
                shared_state["agent_config"] = agent_config
                await AgentContextManager.setup_context(
                    **{
                        **context_setup_kwargs,
                        "config": agent_config,
                        "require_explicit_dataset": True,
                    }
                )
                if context.performance_tracker is not None:
                    context.performance_tracker.mark("knowledge_context_setup")

            if not preflight_ctx and self.agent_service and hasattr(self.agent_service, "_gather_turn_preflight_context"):
                turn_decision = shared_state.get("turn_decision")
                route_details = shared_state.get("route_details")
                if not turn_decision and route_details:
                    turn_decision = getattr(route_details, "turn_decision", None)
                preflight_ctx = await self.agent_service._gather_turn_preflight_context(
                    agent_config=agent_config,
                    user_info=user_info,
                    user_query=str(context.user_query or shared_state.get("user_query") or ""),
                    turn_decision=turn_decision,
                    messages=context.messages,
                    debug_options=debug_options,
                    conversation_id=context.conversation_id,
                )
                shared_state["preflight_ctx"] = preflight_ctx
                if context.performance_tracker is not None:
                    context.performance_tracker.mark("preflight_concurrency_load")

            if preflight_ctx is not None:
                context.ltm_profile = getattr(preflight_ctx, "ltm_profile", None)
                context.ltm_loaded_data = getattr(preflight_ctx, "ltm_loaded_data", None)

        skills_injection = getattr(preflight_ctx, "skills_injection", []) if preflight_ctx else []
        effective_prompt_tool_names = (
            getattr(preflight_ctx, "effective_prompt_tool_names", []) if preflight_ctx else []
        )
        delegable_agent_count = (
            getattr(preflight_ctx, "delegable_agent_count", 0) if preflight_ctx else 0
        )
        roster_loaded = getattr(preflight_ctx, "roster_loaded", False) if preflight_ctx else False
        agent_system_prompt = (
            getattr(preflight_ctx, "agent_system_prompt", None)
            or (getattr(agent_config, "system_prompt", None) if agent_config else "")
            or ""
        )
        sub_agents_context = (
            getattr(preflight_ctx, "sub_agents_context", None) if preflight_ctx else None
        )
        memory_recall_hint = (
            getattr(preflight_ctx, "memory_recall_hint", None) if preflight_ctx else None
        )
        preloaded_memories_text = (
            getattr(preflight_ctx, "preloaded_memories_text", None) if preflight_ctx else None
        )
        user_profile = getattr(preflight_ctx, "user_profile", None) if preflight_ctx else None
        accessible_resources = (
            getattr(preflight_ctx, "accessible_resources", None) if preflight_ctx else None
        )

        if (
            preflight_ctx is not None
            and self.agent_service is not None
            and hasattr(self.agent_service, "_build_skill_log_chunk")
        ):
            for skill_id, skill_name, details_msg in getattr(
                preflight_ctx, "matched_skills_to_log", []
            ):
                yield self.agent_service._build_skill_log_chunk(
                    skill_id,
                    skill_name,
                    details_msg,
                )

        request_knowledge_dataset_ids = shared_state.get("request_knowledge_dataset_ids") or []
        configured_agent_dataset_ids = shared_state.get("configured_agent_dataset_ids") or []
        metadata_dataset_ids = context.metadata_dataset_ids or []
        request_observability = context.request_observability or {}
        ltm_profile = context.ltm_profile
        turn_decision = context.turn_decision or shared_state.get("turn_decision")

        session_scope = (debug_options or {}).get("resource_scope") or {}
        authorized_scope = (request_observability or {}).get("authorized_resource_scope") or {}

        # 1. 产出能力目录日志
        yield _build_capability_catalog_log(
            knowledge_dataset_count=len({
                str(item).strip()
                for item in (request_knowledge_dataset_ids or [])
                if str(item).strip()
            }),
            configured_dataset_count=len({
                str(item).strip()
                for item in configured_agent_dataset_ids
                if str(item).strip()
            }),
            skill_count=len(skills_injection),
            delegable_agent_count=delegable_agent_count,
            roster_loaded=roster_loaded,
            runtime_tool_count=len(effective_prompt_tool_names),
            metadata_dataset_count=len({
                str(item).strip()
                for item in (metadata_dataset_ids or [])
                if str(item).strip()
            }),
            session_dataset_count=len(session_scope.get("datasets", []) or []),
            session_knowledge_base_count=len(session_scope.get("knowledge_bases", []) or []),
            authorized_dataset_count=authorized_scope.get("datasets"),
            authorized_knowledge_base_count=authorized_scope.get("knowledge_bases"),
        )

        # 2. 分层 Prompt 组装
        cache_boundary_enabled, cache_reorder_enabled = await resolve_prompt_assembler_flags()
        engine_type = (
            (getattr(agent_config, "engine_type", None) or "LOCAL") if agent_config else "LOCAL"
        )
        forbid_quick_suggestions = self._should_forbid_quick_suggestions(user_info)

        assembly_input = PromptAssemblyInput(
            agent_system_prompt=agent_system_prompt,
            agent_config=agent_config,
            engine_type=engine_type,
            skills_injection=skills_injection,
            skills_already_loaded=bool(skills_injection),
            skills_dir=getattr(settings, "SKILLS_DIR", "skills"),
            ltm_profile=ltm_profile,
            memory_recall_hint=memory_recall_hint,
            preloaded_memories=preloaded_memories_text,
            user_profile=user_profile,
            accessible_resources=accessible_resources,
            cache_boundary_enabled=cache_boundary_enabled,
            cache_reorder_enabled=cache_reorder_enabled,
            sub_agents_context=sub_agents_context,
            quick_suggestions_forbidden=forbid_quick_suggestions,
            runtime_tool_names=effective_prompt_tool_names,
            turn_decision=turn_decision,
        )
        assembled_prompt = assemble_system_prompt(assembly_input)

        if agent_config:
            agent_config.system_prompt = assembled_prompt.full_text

        # 性能打点
        performance_tracker = context.performance_tracker or shared_state.get("performance_tracker")
        if performance_tracker and hasattr(performance_tracker, "mark"):
            performance_tracker.mark("prompt_assembly")

        if debug_options and debug_options.get("return_raw_prompt"):
            debug_options.setdefault("prompt_assembler_meta", {})
            debug_options["prompt_assembler_meta"] = {
                "stable_chars": len(assembled_prompt.stable_prefix),
                "dynamic_chars": len(assembled_prompt.dynamic_suffix),
                "cache_boundary_enabled": assembled_prompt.cache_boundary_enabled,
                "cache_reorder_enabled": assembled_prompt.cache_reorder_enabled,
                "section_names": list(assembled_prompt.section_names),
                "section_char_counts": assembled_prompt.section_char_counts or {},
            }

        # 3. 调试覆盖 Debug Overrides
        if debug_options:
            if debug_options.get("system_prompt_override"):
                logger.info(f"[Debug] Overriding System Prompt for Trace {trace_id}")
                if agent_config:
                    agent_config.system_prompt = debug_options["system_prompt_override"]
                yield {
                    "type": "log",
                    "title": "Debug: Prompt Override",
                    "details": "System Prompt 已被调试配置临时覆盖",
                    "status": "success",
                    "isDebug": True,
                }

            if debug_options.get("injected_context"):
                context_data = sanitize_injected_context(debug_options["injected_context"])
                logger.info(f"[Debug] Injecting Context: {context_data}")
                ctx_lines = []
                for k, v in context_data.items():
                    if k not in ["device_type", "display_hint", "business_context"]:
                        ctx_lines.append(f"- **{k}**: {v}")
                business_context = context_data.get("business_context")
                if isinstance(business_context, dict):
                    for k, v in business_context.items():
                        ctx_lines.append(f"- **business_context.{k}**: {v}")
                device_type = context_data.get("device_type", "Unknown")
                ui_instr = ""
                if "移动端" in device_type or "小屏幕" in device_type:
                    ui_instr = AgentServicePrompts.MOBILE_UI_RULES
                elif "桌面端" in device_type or "大屏幕" in device_type:
                    ui_instr = AgentServicePrompts.DESKTOP_UI_RULES

                context_str = "\n".join(ctx_lines)
                injection_msg = {
                    "role": "system",
                    "content": AgentServicePrompts.session_runtime_context(
                        context_str, device_type, ui_instr
                    ),
                }
                if context.messages and len(context.messages) >= 2:
                    context.messages.insert(1, injection_msg)
                else:
                    context.messages.append(injection_msg)

        # 4. 注入历史背景与本轮当前请求边界提示词
        final_system_prompt = (
            getattr(agent_config, "system_prompt", None) if agent_config else agent_system_prompt
        )
        bounded_prompt = build_chat_history_boundary_prompt(final_system_prompt)
        if agent_config:
            agent_config.system_prompt = bounded_prompt

        # 5. 发布组装完成与准备阶段日志
        yield _build_prompt_assembly_log(
            assembled_prompt,
            runtime_tool_count=len(effective_prompt_tool_names),
            final_prompt_chars=len(bounded_prompt or ""),
        )

        shared_state["preparation_ready"] = True

        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            import time
            now = time.time()

        raw_started_at = shared_state.get("preparation_started_at") or getattr(context, "start_time", None)
        preparation_started_at = float(raw_started_at) if raw_started_at is not None else now

        yield _build_preparation_parent_log(
            status="success",
            execution_time_ms=max(1.0, (now - preparation_started_at) * 1000),
        )

        if debug_options.get("return_raw_prompt"):
            yield {
                "type": "debug",
                "subtype": "raw_prompt",
                "data": list(context.messages),
            }
