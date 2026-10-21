"""ChatBI native AgentScope turn — main ReAct loop and repair rounds."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List

from app.services.ai.chatbi_sql_user_messages import format_empty_filter_result_content
from app.services.ai.runtime.agentscope.chat import compat_to_runtime_messages, to_agentscope_messages
from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply
from app.services.ai.runtime.agentscope.compat import HumanMessage, AIMessage
from app.services.ai.runners.chatbi.constants import (
    DATA_REPAIR_BUDGETS,
    MAX_DATA_REPAIR_ROUNDS,
    MAX_REPAIR_WALL_SECONDS,
)
from app.services.ai.runners.chatbi.forced_tool_choice import ForcedFirstToolChoiceModel
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.repair_controller import ChatBIRepairController
from app.services.ai.runners.chatbi.insight_meta import take_chatbi_insight_meta_event
from app.services.ai.runners.chatbi.sql_result_compact import (
    mark_deferred_continue_query,
    should_force_deferred_continue_query,
    should_rescue_contradictory_empty_reply,
    should_rescue_deferred_sql_reply,
    should_rescue_process_only_after_sql,
    should_rescue_sql_without_followup_content,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

logger = logging.getLogger(__name__)


async def _persist_agent_state(
    runner: Any,
    *,
    agent_name: str,
    tools_fingerprint: str,
    model_name: Any,
    agent: Any,
) -> None:
    if not runner.conversation_id:
        return
    await agent_state_store.save(
        user_id=runner._runtime_user_id(),
        conversation_id=runner.conversation_id,
        agent_name=agent_name,
        agent_version=runner.config.agent_version,
        tools_fingerprint=tools_fingerprint,
        model_name=str(model_name) if model_name else None,
        state=agent.state,
    )


async def _retract_and_synthesize_from_cache(
    runner: Any,
    *,
    state: DataRunState,
    runtime_messages: List[Any],
    system_content: str,
    user_question: str,
    agent_name: str,
    tools_fingerprint: str,
    model_name: Any,
    agent: Any,
    reason: str,
    retract_reason: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    if state.full_content:
        async for chunk in runner._retract_provisional_content_before_repair(
            state,
            reason=retract_reason,
        ):
            yield chunk
    async for chunk in runner._synthesize_from_cached_sql_result(
        runtime_messages=runtime_messages,
        system_prompt=system_content,
        user_question=user_question,
        state=state,
        reason=reason,
    ):
        yield chunk
    insight_event = take_chatbi_insight_meta_event(
        state,
        evidence_metadata=getattr(runner, "_evidence_metadata", None),
    )
    if insight_event is not None:
        yield insight_event
    await _persist_agent_state(
        runner,
        agent_name=agent_name,
        tools_fingerprint=tools_fingerprint,
        model_name=model_name,
        agent=agent,
    )


async def _finalize_content_and_persist(
    runner: Any,
    *,
    state: DataRunState,
    agent_name: str,
    tools_fingerprint: str,
    model_name: Any,
    agent: Any,
) -> AsyncGenerator[Dict[str, Any], None]:
    deduped = finalize_visible_reply(state.full_content)
    if deduped != state.full_content:
        logger.warning(
            "[DataAgentRunner] Collapsed duplicated content (%d -> %d chars)",
            len(state.full_content),
            len(deduped),
        )
        state.full_content = deduped
        yield {"type": "retraction", "content": deduped}
    evidence_result = (
        state.last_successful_sql_output
        if state.requires_sql_query
        else state.schema_output
    )
    grounding_audit = runner._chatbi_grounding_audit(
        candidate_text=state.full_content,
        evidence_result=evidence_result,
    )
    if grounding_audit.should_warn:
        warning = grounding_audit.warning_chunk
        yield {
            "type": "log",
            "id": f"chatbi_grounding_{uuid.uuid4().hex[:8]}",
            "title": "事实来源风险提示已追加",
            "details": warning["grounding_risk"]["reason"],
            "status": "warning",
            "category": "grounding",
        }
        state.full_content += str(warning.get("content") or "")
        yield warning
    insight_event = take_chatbi_insight_meta_event(
        state,
        evidence_metadata=getattr(runner, "_evidence_metadata", None),
    )
    if insight_event is not None:
        yield insight_event
    await _persist_agent_state(
        runner,
        agent_name=agent_name,
        tools_fingerprint=tools_fingerprint,
        model_name=model_name,
        agent=agent,
    )


async def run_native_agent_turn(
    runner: Any,
    *,
    native_model: Any,
    tools: list[RuntimeToolSpec],
    tools_fingerprint: str,
    model_name: Any,
    agent_name: str,
    system_content: str,
    max_steps: int,
    llm_handle: Any,
    runtime_messages: List[Any],
    user_question: str,
    turn_cls: Any,
    prefetched_schema_output: str | None = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    await runner._ensure_schema_similarity_threshold()
    restored_state = None
    if not turn_cls.requires_fresh_data:
        persisted = await agent_state_store.load(
            runner._runtime_user_id(),
            runner.conversation_id,
            agent_name,
        )
        if persisted:
            if persisted.matches(tools_fingerprint=tools_fingerprint, agent_name=agent_name):
                try:
                    from agentscope.state import AgentState

                    restored_state = AgentState.model_validate(persisted.state)
                except Exception as exc:
                    logger.warning("[DataAgentRunner] Failed to restore AgentState: %s", exc)
            else:
                logger.warning(
                    "[DataAgentRunner] Tools fingerprint mismatch for agent=%s (stored=%s, current=%s). "
                    "Resetting conversation state to prevent tool call conflicts.",
                    agent_name,
                    persisted.tools_fingerprint,
                    tools_fingerprint,
                )
                yield {
                    "type": "log",
                    "id": f"state_reset_{uuid.uuid4().hex[:8]}",
                    "title": "智能体配置变更：历史会话状态已重置",
                    "details": "检测到绑定的工具集或模型配置发生改变，为防工具调用崩溃，已重置运行时状态。",
                    "status": "warning",
                }

    state = DataRunState()
    state.requires_fresh_data = turn_cls.requires_fresh_data
    state.requires_sql_query = bool(getattr(turn_cls, "requires_sql_query", True))
    state.requires_sql_plan = (
        runner._is_sql_plan_enabled()
        and state.requires_fresh_data
        and state.requires_sql_query
        and runner._should_require_sql_plan(user_question)
    )
    if prefetched_schema_output is not None:
        state.last_schema_keywords = (
            runner._schema_search_keywords or runner._standalone_query or user_question or ""
        ).strip()
        runner._apply_schema_tool_result(state, prefetched_schema_output)
        if state.schema_miss:
            runner._prepare_controlled_schema_retry_keywords(state, user_question)
        if runner._is_schema_fatal(state):
            async for chunk in runner._yield_schema_fatal_abort(state, prefetched_schema_output):
                yield chunk
            return

    guarded_tools = runner._wrap_tools_with_schema_gate(tools, state)
    agent = await runner._build_native_agent(
        native_model=native_model,
        tools=guarded_tools,
        system_content=system_content,
        max_steps=max_steps,
        restored_state=restored_state,
        primary_model_name=str(getattr(llm_handle, "model_name", runner.config.model_name) or ""),
    )
    if runner._standalone_query and runner._standalone_query != user_question:
        conv_only = [m for m in runtime_messages[:-1] if isinstance(m, (HumanMessage, AIMessage))]
        last_history = conv_only[-6:]
        runtime_messages = [*last_history, HumanMessage(content=runner._standalone_query)]
    if restored_state and restored_state.context:
        inputs = to_agentscope_messages(
            compat_to_runtime_messages(runner._latest_user_runtime_messages(runtime_messages))
        )
    else:
        inputs = to_agentscope_messages(compat_to_runtime_messages(runtime_messages))

    stream_meta = {
        "system_content": system_content,
        "max_steps": max_steps,
        "user_question": user_question,
    }
    interrupted = False
    initial_tool_choice = runner._resolve_initial_tool_choice(state)
    original_model = agent.model
    if state.schema_miss:
        logger.info(
            "[DataAgentRunner] Prefetch schema miss, skipping initial LLM interaction and entering repair directly."
        )
    else:
        if initial_tool_choice is not None:
            agent.model = ForcedFirstToolChoiceModel(original_model, initial_tool_choice)
        try:
            async for chunk in runner._stream_agentscope_events(
                event_stream=agent.reply_stream(inputs),
                agent=agent,
                tools=guarded_tools,
                native_model=native_model,
                state=state,
                stream_meta=stream_meta,
                emit_final_guard=False,
            ):
                if is_interrupt_sse_chunk(chunk):
                    interrupted = True
                yield chunk
        finally:
            agent.model = original_model

    if interrupted:
        return
    if runner._is_schema_fatal(state):
        async for chunk in runner._yield_schema_fatal_abort(state):
            yield chunk
        return
    if state.full_content and runner._current_repair_kind(state):
        async for chunk in runner._retract_provisional_content_before_repair(
            state,
            reason="main-loop content followed by a repair condition",
        ):
            yield chunk
    if (
        state.full_content
        and not runner._current_repair_kind(state)
        and should_force_deferred_continue_query(state)
    ):
        mark_deferred_continue_query(state)
        if runner._repair_budget_exhausted(state):
            state.deferred_continue_query = False
        else:
            async for chunk in runner._retract_provisional_content_before_repair(
                state,
                reason="deferred continue-query reply after successful SQL",
            ):
                yield chunk
            # 保留 deferred_continue_query，落入下方 repair 循环强制再跑一轮 SQL。
    if (
        not runner._current_repair_kind(state)
        and should_rescue_sql_without_followup_content(state)
    ):
        async for chunk in _retract_and_synthesize_from_cache(
            runner,
            state=state,
            runtime_messages=runtime_messages,
            system_content=system_content,
            user_question=user_question,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
            reason="sql_without_followup_content",
            retract_reason="successful SQL after last visible content without follow-up answer",
        ):
            yield chunk
        return
    if (
        state.full_content
        and not runner._current_repair_kind(state)
        and should_rescue_process_only_after_sql(state)
    ):
        async for chunk in _retract_and_synthesize_from_cache(
            runner,
            state=state,
            runtime_messages=runtime_messages,
            system_content=system_content,
            user_question=user_question,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
            reason="process_only_after_sql",
            retract_reason="process-only reply after successful SQL",
        ):
            yield chunk
        return
    if (
        state.full_content
        and not runner._current_repair_kind(state)
        and should_rescue_contradictory_empty_reply(state)
    ):
        async for chunk in _retract_and_synthesize_from_cache(
            runner,
            state=state,
            runtime_messages=runtime_messages,
            system_content=system_content,
            user_question=user_question,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
            reason="contradictory_empty_reply",
            retract_reason="contradictory empty reply after successful SQL",
        ):
            yield chunk
        return
    if (
        state.full_content
        and not runner._current_repair_kind(state)
        and should_rescue_deferred_sql_reply(state)
    ):
        async for chunk in _retract_and_synthesize_from_cache(
            runner,
            state=state,
            runtime_messages=runtime_messages,
            system_content=system_content,
            user_question=user_question,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
            reason="deferred_incomplete_reply",
            retract_reason="deferred incomplete reply after successful SQL",
        ):
            yield chunk
        return
    if state.full_content and not runner._current_repair_kind(state):
        async for chunk in _finalize_content_and_persist(
            runner,
            state=state,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
        ):
            yield chunk
        return

    if state.platform_auto_retry_ready and state.last_successful_sql_output is not None:
        if state.full_content:
            async for chunk in runner._retract_provisional_content_before_repair(
                state,
                reason="platform auto-retry success supersedes provisional reply",
            ):
                yield chunk
        async for chunk in runner._synthesize_from_cached_sql_result(
            runtime_messages=runtime_messages,
            system_prompt=system_content,
            user_question=user_question,
            state=state,
            reason="platform_auto_retry",
        ):
            yield chunk
        state.platform_auto_retry_ready = False
        insight_event = take_chatbi_insight_meta_event(
            state,
            evidence_metadata=getattr(runner, "_evidence_metadata", None),
        )
        if insight_event is not None:
            yield insight_event
        await _persist_agent_state(
            runner,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
        )
        return

    if state.sql_repeat_gate_block and state.last_successful_sql_output is not None:
        async for chunk in runner._synthesize_from_cached_sql_result(
            runtime_messages=runtime_messages,
            system_prompt=system_content,
            user_question=user_question,
            state=state,
        ):
            yield chunk
        insight_event = take_chatbi_insight_meta_event(
            state,
            evidence_metadata=getattr(runner, "_evidence_metadata", None),
        )
        if insight_event is not None:
            yield insight_event
        await _persist_agent_state(
            runner,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
        )
        return

    max_repair_rounds = max(sum(DATA_REPAIR_BUDGETS.values()), MAX_DATA_REPAIR_ROUNDS)
    repair_controller = (
        runner._repair_controller(state)
        if hasattr(runner, "_repair_controller")
        else ChatBIRepairController(
            state,
            semantic_intent=getattr(runner, "_semantic_intent", None),
        )
    )
    repair_started_at = time.time()
    for _ in range(max_repair_rounds):
        if state.sql_fatal_error:
            break
        if time.time() - repair_started_at > MAX_REPAIR_WALL_SECONDS:
            logger.warning(
                "[DataAgentRunner] Repair wall-clock budget exhausted (%.0fs), stop opening new repair rounds.",
                MAX_REPAIR_WALL_SECONDS,
            )
            break
        repair_decision = repair_controller.decide()
        if repair_decision is None:
            break
        if not repair_decision.message:
            break
        yield {
            "type": "log",
            "id": f"data_repair_{uuid.uuid4().hex[:8]}",
            "title": repair_decision.title,
            "details": repair_decision.message,
            "status": "warning",
            "repair_kind": repair_decision.kind,
            "repair_attempt": repair_decision.attempt + 1,
            "repair_budget": repair_decision.budget,
        }
        repair_controller.begin(repair_decision)
        repair_message = repair_decision.message
        repair_tool_choice = repair_decision.tool_choice
        repair_inputs = to_agentscope_messages(compat_to_runtime_messages(repair_message))
        original_model = agent.model
        if repair_tool_choice is not None:
            agent.model = ForcedFirstToolChoiceModel(original_model, repair_tool_choice)
        try:
            async for chunk in runner._stream_agentscope_events(
                event_stream=agent.reply_stream(repair_inputs),
                agent=agent,
                tools=guarded_tools,
                native_model=native_model,
                state=state,
                stream_meta=stream_meta,
                emit_final_guard=False,
            ):
                if is_interrupt_sse_chunk(chunk):
                    interrupted = True
                yield chunk
        finally:
            agent.model = original_model
        if interrupted:
            return
        if runner._is_schema_fatal(state):
            async for chunk in runner._yield_schema_fatal_abort(state):
                yield chunk
            return
        if state.sql_fatal_error:
            async for chunk in runner._yield_sql_fatal_abort(state):
                yield chunk
            return
        if state.full_content and runner._current_repair_kind(state):
            async for chunk in runner._retract_provisional_content_before_repair(
                state,
                reason="repair-loop content followed by a repair condition",
            ):
                yield chunk
        if state.full_content and runner._should_replace_generic_empty_failure_reply(state):
            state.full_content = format_empty_filter_result_content(state.empty_filter_diagnostics)
        if (
            state.full_content
            and not runner._current_repair_kind(state)
            and should_force_deferred_continue_query(state)
        ):
            mark_deferred_continue_query(state)
            if runner._repair_budget_exhausted(state):
                state.deferred_continue_query = False
            else:
                async for chunk in runner._retract_provisional_content_before_repair(
                    state,
                    reason="deferred continue-query reply after repair-loop SQL",
                ):
                    yield chunk
                continue
        if (
            not runner._current_repair_kind(state)
            and should_rescue_sql_without_followup_content(state)
        ):
            async for chunk in _retract_and_synthesize_from_cache(
                runner,
                state=state,
                runtime_messages=runtime_messages,
                system_content=system_content,
                user_question=user_question,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
                reason="sql_without_followup_content",
                retract_reason="successful SQL after last visible content without follow-up answer (repair loop)",
            ):
                yield chunk
            return
        if (
            state.full_content
            and not runner._current_repair_kind(state)
            and should_rescue_process_only_after_sql(state)
        ):
            async for chunk in _retract_and_synthesize_from_cache(
                runner,
                state=state,
                runtime_messages=runtime_messages,
                system_content=system_content,
                user_question=user_question,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
                reason="process_only_after_sql",
                retract_reason="process-only reply after repair-loop SQL",
            ):
                yield chunk
            return
        if (
            state.full_content
            and not runner._current_repair_kind(state)
            and should_rescue_contradictory_empty_reply(state)
        ):
            async for chunk in _retract_and_synthesize_from_cache(
                runner,
                state=state,
                runtime_messages=runtime_messages,
                system_content=system_content,
                user_question=user_question,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
                reason="contradictory_empty_reply",
                retract_reason="contradictory empty reply after repair-loop SQL",
            ):
                yield chunk
            return
        if (
            state.full_content
            and not runner._current_repair_kind(state)
            and should_rescue_deferred_sql_reply(state)
        ):
            async for chunk in _retract_and_synthesize_from_cache(
                runner,
                state=state,
                runtime_messages=runtime_messages,
                system_content=system_content,
                user_question=user_question,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
                reason="deferred_incomplete_reply",
                retract_reason="deferred incomplete reply after repair-loop SQL",
            ):
                yield chunk
            return
        if state.platform_auto_retry_ready and state.last_successful_sql_output is not None:
            if state.full_content:
                async for chunk in runner._retract_provisional_content_before_repair(
                    state,
                    reason="platform auto-retry success after repair-loop SQL",
                ):
                    yield chunk
            async for chunk in runner._synthesize_from_cached_sql_result(
                runtime_messages=runtime_messages,
                system_prompt=system_content,
                user_question=user_question,
                state=state,
                reason="platform_auto_retry",
            ):
                yield chunk
            state.platform_auto_retry_ready = False
            insight_event = take_chatbi_insight_meta_event(
                state,
                evidence_metadata=getattr(runner, "_evidence_metadata", None),
            )
            if insight_event is not None:
                yield insight_event
            await _persist_agent_state(
                runner,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
            )
            return
        if state.full_content and not runner._current_repair_kind(state):
            async for chunk in _finalize_content_and_persist(
                runner,
                state=state,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
            ):
                yield chunk
            return
        if state.sql_repeat_gate_block and state.last_successful_sql_output is not None:
            async for chunk in runner._synthesize_from_cached_sql_result(
                runtime_messages=runtime_messages,
                system_prompt=system_content,
                user_question=user_question,
                state=state,
            ):
                yield chunk
            insight_event = take_chatbi_insight_meta_event(
                state,
                evidence_metadata=getattr(runner, "_evidence_metadata", None),
            )
            if insight_event is not None:
                yield insight_event
            await _persist_agent_state(
                runner,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
            )
            return

    # Soft tip：时长疑似异常且预算用尽时，优先基于已缓存结果合成回答，避免硬拦截无答。
    if state.duration_anomaly and state.last_successful_sql_output is not None:
        tip = (
            "【系统提示】时延/时长字段可能异常（未硬拦截）："
            f"{state.duration_anomaly_reason or '请核对时间字段方向、时区与单位'}。"
            "以下基于已查询结果继续回答。"
        )
        yield {
            "type": "log",
            "id": f"duration_tip_{uuid.uuid4().hex[:8]}",
            "title": "时间差/时延结果提示",
            "details": tip,
            "status": "warning",
        }
        state.duration_anomaly = False
        async for chunk in runner._synthesize_from_cached_sql_result(
            runtime_messages=runtime_messages,
            system_prompt=system_content,
            user_question=user_question,
            state=state,
        ):
            yield chunk
        insight_event = take_chatbi_insight_meta_event(
            state,
            evidence_metadata=getattr(runner, "_evidence_metadata", None),
        )
        if insight_event is not None:
            yield insight_event
        if not interrupted:
            await _persist_agent_state(
                runner,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
            )
        return

    if state.sql_fatal_error:
        async for chunk in runner._yield_sql_fatal_abort(state):
            yield chunk
        if not interrupted:
            await _persist_agent_state(
                runner,
                agent_name=agent_name,
                tools_fingerprint=tools_fingerprint,
                model_name=model_name,
                agent=agent,
            )
        return

    async for chunk in runner._emit_final_guard(state):
        yield chunk
    if not interrupted:
        await _persist_agent_state(
            runner,
            agent_name=agent_name,
            tools_fingerprint=tools_fingerprint,
            model_name=model_name,
            agent=agent,
        )
