"""ChatBI synthesis — extracted from DataAgentRunner."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

from app.schemas.agent import AgentExecutionStep
from app.services.ai.temperature import (
    coerce_temperature,
    resolve_config_temperature,
)
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.synthesis_stream import (
    SynthesisStreamState,
    result_dict_to_json,
    stream_synthesis_llm_chunks,
    synthesis_token_usage,
)

logger = logging.getLogger(__name__)


def _chatbi_grounding_events(
    runner: Any,
    *,
    stream_state: SynthesisStreamState,
    evidence_result: Any,
) -> list[Dict[str, Any]]:
    grounding_audit = runner._chatbi_grounding_audit(
        candidate_text=stream_state.full_content,
        evidence_result=evidence_result,
    )
    if not grounding_audit.should_warn:
        return []
    warning = grounding_audit.warning_chunk
    stream_state.full_content += str(warning.get("content") or "")
    return [
        {
            "type": "log",
            "id": f"chatbi_grounding_{uuid.uuid4().hex[:8]}",
            "title": "事实来源风险提示已追加",
            "details": warning["grounding_risk"]["reason"],
            "status": "warning",
            "category": "grounding",
        },
        warning,
    ]


def _recent_human_messages(runtime_messages: List[Any]) -> List[Any]:
    return [
        message
        for message in runtime_messages[-6:-1]
        if isinstance(message, HumanMessage) and getattr(message, "content", None)
    ]


def _prompt_without_dataset_menu(system_prompt: str) -> str:
    return (system_prompt or "").replace(
        "{dataset_menu}",
        DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
    )


def _append_synthesis_trace(
    runner: Any,
    *,
    start_synthesis: float,
    stream_state: SynthesisStreamState,
    tool_output: Dict[str, Any],
) -> None:
    tokens = synthesis_token_usage(stream_state)
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="synthesis",
            agent_name=runner.config.agent_name,
            model=str(
                getattr(
                    stream_state.final_llm,
                    "model_name",
                    runner.config.synthesis_model_name or runner.config.model_name,
                )
            ),
            temperature=(
                coerce_temperature(runner.config.synthesis_temperature)
                if coerce_temperature(runner.config.synthesis_temperature) is not None
                else resolve_config_temperature(runner.config)
            ),
            tool_output=tool_output,
            raw_log=stream_state.full_content,
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )


async def synthesize_from_last_data_result(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    last_result: Dict[str, Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"reuse_{uuid.uuid4().hex[:8]}",
        "title": "复用上一轮查询结果",
        "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    result_json = result_dict_to_json(last_result)
    synthesis_messages = [SystemMessage(content=_prompt_without_dataset_menu(system_prompt))]
    synthesis_messages.extend(_recent_human_messages(runtime_messages))
    synthesis_messages.append(
        HumanMessage(
            content=DataQueryPrompts.followup_synthesis_user_message(user_question, result_json)
        )
    )

    stream_state = SynthesisStreamState()
    async for chunk in stream_synthesis_llm_chunks(
        runner,
        synthesis_messages,
        stream_state,
        start_title="✨ 开始生成回复",
        complete_title="✨ 生成回复完成",
        error_title="⚠️ 总结生成失败",
        fallback=DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK,
        dedupe_warning_context="[DataAgentRunner] Collapsed duplicated follow-up synthesis output",
    ):
        yield chunk

    for event in _chatbi_grounding_events(
        runner,
        stream_state=stream_state,
        evidence_result=last_result,
    ):
        yield event

    _append_synthesis_trace(
        runner,
        start_synthesis=start_synthesis,
        stream_state=stream_state,
        tool_output={"content": stream_state.full_content, "reused_last_data_result": True},
    )


async def synthesize_format_correction(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    last_result: Dict[str, Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"format_{uuid.uuid4().hex[:8]}",
        "title": "样式与图表微调",
        "details": "检测到图表样式或展示微调请求，直接复用上一轮数据，无需重新查数。",
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    result_json = result_dict_to_json(last_result)
    synthesis_messages = [SystemMessage(content=_prompt_without_dataset_menu(system_prompt))]
    synthesis_messages.extend(_recent_human_messages(runtime_messages))
    synthesis_messages.append(
        HumanMessage(
            content=DataQueryPrompts.format_correction_user_message(user_question, result_json)
        )
    )

    stream_state = SynthesisStreamState()
    async for chunk in stream_synthesis_llm_chunks(
        runner,
        synthesis_messages,
        stream_state,
        start_title="✨ 开始生成微调样式",
        complete_title="✨ 微调样式生成完成",
        error_title="⚠️ 样式生成失败",
        fallback=DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK,
    ):
        yield chunk

    for event in _chatbi_grounding_events(
        runner,
        stream_state=stream_state,
        evidence_result=last_result,
    ):
        yield event

    _append_synthesis_trace(
        runner,
        start_synthesis=start_synthesis,
        stream_state=stream_state,
        tool_output={"content": stream_state.full_content, "reused_last_data_result": True},
    )


async def synthesize_from_history_data_result(
    runner: Any,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    history: List[Dict[str, str]],
) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"reuse_hist_{uuid.uuid4().hex[:8]}",
        "title": "复用上一轮查询结果",
        "details": (
            "检测到本轮是基于上一轮结果的分析/可视化请求；结构化缓存暂不可用，"
            "已基于最近对话中的查数展示继续处理。"
        ),
        "status": "success",
    }
    yield {"type": "thinking", "status": "continuing"}

    history_excerpt = runner._latest_data_assistant_excerpt(history)
    synthesis_messages = [SystemMessage(content=_prompt_without_dataset_menu(system_prompt))]
    synthesis_messages.extend(_recent_human_messages(runtime_messages))
    synthesis_messages.append(
        HumanMessage(
            content=DataQueryPrompts.followup_synthesis_from_history_user_message(
                user_question,
                history_excerpt,
            )
        )
    )

    stream_state = SynthesisStreamState()
    async for chunk in stream_synthesis_llm_chunks(
        runner,
        synthesis_messages,
        stream_state,
        start_title="✨ 开始生成回复",
        complete_title="✨ 生成回复完成",
        error_title="⚠️ 总结生成失败",
        fallback=DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK,
    ):
        yield chunk

    for event in _chatbi_grounding_events(
        runner,
        stream_state=stream_state,
        evidence_result=None,
    ):
        yield event

    _append_synthesis_trace(
        runner,
        start_synthesis=start_synthesis,
        stream_state=stream_state,
        tool_output={"content": stream_state.full_content, "reused_history_data_result": True},
    )


async def synthesize_from_cached_sql_result(
    runner: Any,
    *,
    runtime_messages: List[Any],
    system_prompt: str,
    user_question: str,
    state: DataRunState,
    reason: str = "repeat_sql",
) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    if reason == "deferred_incomplete_reply":
        log_title = "补全未完成的汇总回答"
        log_details = (
            "检测到模型在查数成功后仅输出过渡句（如「读取完整数据后再汇总」）即停轮。"
            "平台已撤回半截回复，并基于本轮成功查询结果生成完整回答。"
        )
        review_reason = (
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 随后模型只输出了过渡性承诺而未给出实质汇总，平台已撤回该半截回复并复用查询结果。"
        )
        reused_flag = "reused_deferred_incomplete_reply"
    elif reason == "platform_auto_retry":
        log_title = "基于平台自动修正结果生成回答"
        log_details = (
            "平台已自动修正筛选/WHERE 并重试成功，但模型侧仍可能只看到修正前的空结果。"
            "已停止半截 ReAct，并基于自动重试后的成功结果生成最终回答。"
        )
        review_reason = (
            "- 首次 SQL 为空或 WHERE 失败后，平台已自动修正并重试成功。\n"
            "- 为避免模型继续基于旧的空/错结果回答，平台已改用自动重试后的成功查询结果。"
        )
        reused_flag = "reused_platform_auto_retry_result"
    elif reason == "contradictory_empty_reply":
        log_title = "纠正空结果误述并补全回答"
        log_details = (
            "检测到模型正文仍宣称「查询返回为空」或仅准备探查分布，但本轮已有成功非空查询结果。"
            "平台已撤回该误述，并基于成功结果生成完整回答。"
        )
        review_reason = (
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 随后模型仍输出「查询返回为空 / 先查看分布」类半截话，与结果冲突，平台已撤回并复用查询结果。"
        )
        reused_flag = "reused_contradictory_empty_reply"
    elif reason == "sql_without_followup_content":
        log_title = "补全工具后的最终回答"
        log_details = (
            "检测到最新成功 SQL 发生在最后一次可见正文之后（模型在查数前/中输出过程话后停轮）。"
            "平台已撤回旧正文，并基于最新成功查询结果生成完整回答。"
        )
        review_reason = (
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 该成功 SQL 发生在最后一次可见正文之后，模型未再给出实质汇总；平台已撤回旧过程话并复用查询结果。"
        )
        reused_flag = "reused_sql_without_followup_content"
    elif reason == "process_only_after_sql":
        log_title = "补全过程句后的最终回答"
        log_details = (
            "检测到查数成功后正文仍仅为「现在查询 / 继续分析」等过程句。"
            "平台已撤回该过程话，并基于成功查询结果生成完整回答。"
        )
        review_reason = (
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 随后模型只输出过程性叙述而未给出实质汇总，平台已撤回并复用查询结果。"
        )
        reused_flag = "reused_process_only_after_sql"
    else:
        log_title = "复用已执行 SQL 结果"
        log_details = (
            "检测到模型重复调用相同 SQL。平台已拦截重复执行，并基于首次成功查询结果生成最终回答。"
        )
        review_reason = (
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 随后模型重复调用相同 SQL，平台已拦截重复执行并复用首次成功查询结果。"
        )
        reused_flag = "reused_repeated_sql_result"

    yield {
        "type": "log",
        "id": f"repeat_sql_{uuid.uuid4().hex[:8]}",
        "title": log_title,
        "details": log_details,
        "status": "success",
    }

    raw_result = state.last_successful_sql_output
    parsed_result = runner._try_parse_json_output(raw_result)
    runner._update_evidence_metadata(parsed_result)
    result_json = result_dict_to_json(parsed_result, max_chars=20000)
    evidence = runner._evidence_metadata or {}
    evidence_context = (
        "【证据元数据（必须如实反映到最终回答）】\n"
        f"- 结果状态：{evidence.get('status') or 'unknown'}\n"
        f"- 数据来源：{evidence.get('source_ref') or '未识别'}\n"
        f"- 观测时间：{evidence.get('observed_at') or '未提供'}\n"
        f"- 数据截至：{evidence.get('source_as_of') or '未提供'}\n"
        f"- 时效：{evidence.get('freshness') or 'unknown'}"
    )
    execution_review = (
        "【执行过程回顾】\n"
        f"{review_reason}\n\n"
        "【查询结果】\n"
        f"{result_json}"
    )
    synthesis_messages = [SystemMessage(content=_prompt_without_dataset_menu(system_prompt))]
    synthesis_messages.extend(_recent_human_messages(runtime_messages))
    synthesis_messages.append(
        HumanMessage(
            content=DataQueryPrompts.synthesis_user_message(
                user_question,
                execution_review,
                evidence_context=evidence_context,
            )
        )
    )

    stream_state = SynthesisStreamState()
    async for chunk in stream_synthesis_llm_chunks(
        runner,
        synthesis_messages,
        stream_state,
        start_title="✨ 开始生成回复",
        complete_title="✨ 生成回复完成",
        error_title="⚠️ 总结生成失败",
        fallback=DataQueryPrompts.SYNTHESIS_FAILED_FALLBACK,
        dedupe_warning_context="[DataAgentRunner] Collapsed duplicated cached SQL synthesis output",
    ):
        yield chunk

    for event in _chatbi_grounding_events(
        runner,
        stream_state=stream_state,
        evidence_result=parsed_result,
    ):
        yield event

    _append_synthesis_trace(
        runner,
        start_synthesis=start_synthesis,
        stream_state=stream_state,
        tool_output={"content": stream_state.full_content, reused_flag: True},
    )
