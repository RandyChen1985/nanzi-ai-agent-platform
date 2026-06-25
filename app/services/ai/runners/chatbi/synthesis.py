"""ChatBI synthesis — extracted from DataAgentRunner."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

from app.schemas.agent import AgentExecutionStep
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
            temperature=float(
                runner.config.synthesis_temperature or runner.config.temperature or 0
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
) -> AsyncGenerator[Dict[str, Any], None]:
    start_synthesis = time.time()
    yield {
        "type": "log",
        "id": f"repeat_sql_{uuid.uuid4().hex[:8]}",
        "title": "复用已执行 SQL 结果",
        "details": "检测到模型重复调用相同 SQL。平台已拦截重复执行，并基于首次成功查询结果生成最终回答。",
        "status": "success",
    }

    raw_result = state.last_successful_sql_output
    parsed_result = runner._try_parse_json_output(raw_result)
    result_json = result_dict_to_json(parsed_result, max_chars=20000)
    execution_review = (
        "【执行过程回顾】\n"
        "- 已成功执行 SQL 并获得非空结果。\n"
        "- 随后模型重复调用相同 SQL，平台已拦截重复执行并复用首次成功查询结果。\n\n"
        "【查询结果】\n"
        f"{result_json}"
    )
    synthesis_messages = [SystemMessage(content=_prompt_without_dataset_menu(system_prompt))]
    synthesis_messages.extend(_recent_human_messages(runtime_messages))
    synthesis_messages.append(
        HumanMessage(content=DataQueryPrompts.synthesis_user_message(user_question, execution_review))
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

    _append_synthesis_trace(
        runner,
        start_synthesis=start_synthesis,
        stream_state=stream_state,
        tool_output={"content": stream_state.full_content, "reused_repeated_sql_result": True},
    )
