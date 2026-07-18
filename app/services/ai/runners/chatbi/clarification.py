"""ChatBI clarification — extracted from DataAgentRunner."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator, Dict, List

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)


def _resolve_agent_display_name(runner: Any) -> str:
    config = getattr(runner, "config", None)
    display = getattr(config, "agent_display_name", None) if config else None
    name = getattr(config, "agent_name", None) if config else None
    return DataQueryPrompts.resolve_agent_display_name(display or name)


def _agent_brief(runner: Any) -> str | None:
    config = getattr(runner, "config", None)
    system_prompt = str(getattr(config, "system_prompt", None) or "").strip()
    if not system_prompt:
        return None
    first_line = system_prompt.splitlines()[0].strip()
    return first_line[:160] if first_line else None


def _build_user_profile_block(runner: Any) -> str | None:
    if not runner.user_info:
        return None
    from app.services.ai.agent_prompts import AgentServicePrompts

    raw_name = runner.user_info.get("user_name") or runner.user_info.get("username", "Unknown User")
    user_id = str(runner.user_info.get("user_id") or runner.user_info.get("id") or "")
    real_name = runner.user_info.get("real_name") or raw_name
    dept = runner.user_info.get("dept_name") or runner.user_info.get("department")
    org_path = runner.user_info.get("org_path")
    dept_code = runner.user_info.get("dept_code")
    role = runner.user_info.get("role_name") or runner.user_info.get("role")
    return AgentServicePrompts.user_context_message(
        user_id=user_id or "unknown",
        raw_name=raw_name,
        real_name=real_name,
        dept=dept,
        dept_code=dept_code,
        org_path=org_path,
        role=role,
    )


async def generate_clarification_content(
    runner: Any,
    *,
    user_question: str,
    history: List[Dict[str, str]],
    reasoning: str,
    missing_fields: tuple[str, ...] | None = None,
) -> str:
    history_excerpt = DataQueryPrompts.format_clarification_history(history)
    user_profile = _build_user_profile_block(runner)
    agent_display_name = _resolve_agent_display_name(runner)
    scenario = DataQueryPrompts.resolve_clarification_scenario(user_question, reasoning)
    skip_llm = (
        missing_fields is not None
        or DataQueryPrompts.should_skip_clarification_llm(scenario)
    )
    llm_lead: str | None = None
    suggested_queries: tuple[str, ...] = ()
    quick_source = "rule"

    if missing_fields:
        try:
            llm = await AgentConfigProvider.get_configured_llm(
                streaming=False,
                config=runner.config,
            )
            chat_client = chat_client_from_handle(llm)
            raw_recommendations = await chat_client.generate_text(
                system_user_prompt_messages(
                    DataQueryPrompts.clarification_recommendation_prompt(
                        user_question,
                        history_excerpt,
                        missing_fields,
                    ),
                    user_prompt=user_question,
                )
            )
            match = re.search(r"\{.*\}", str(raw_recommendations or ""), flags=re.DOTALL)
            payload = json.loads(match.group() if match else str(raw_recommendations or ""))
            suggested_queries = DataQueryPrompts.validate_clarification_recommendations(
                user_question,
                missing_fields,
                payload.get("queries") if isinstance(payload, dict) else (),
            )
            if suggested_queries:
                quick_source = "llm_recommendations"
        except Exception as e:
            logger.warning(
                "[DataAgentRunner] Contextual clarification recommendation generation failed: %s",
                e,
            )

    if not skip_llm:
        try:
            llm = await AgentConfigProvider.get_configured_llm(
                streaming=False,
                config=runner.config,
            )
            chat_client = chat_client_from_handle(llm)
            raw_lead = await chat_client.generate_text(
                system_user_prompt_messages(
                    DataQueryPrompts.clarification_lead_generation_prompt(
                        scenario,
                        user_question,
                        reasoning,
                        history_excerpt,
                        user_profile=user_profile,
                        agent_display_name=agent_display_name,
                    ),
                    user_prompt=user_question,
                )
            )
            if DataQueryPrompts.is_valid_clarification_lead(
                raw_lead, user_question, reasoning
            ):
                llm_lead = DataQueryPrompts.sanitize_clarification_lead(raw_lead)
                quick_source = "llm_lead+rule_quick"
            else:
                logger.info(
                    "[DataAgentRunner] Clarification LLM lead rejected; using rule lead. scenario=%s",
                    scenario,
                )
        except Exception as e:
            logger.warning(
                "[DataAgentRunner] Contextual clarification lead generation failed: %s",
                e,
            )

    content = DataQueryPrompts.build_clarification_response(
        user_question,
        reasoning,
        history_excerpt,
        lead=llm_lead,
        missing_fields=missing_fields,
        suggested_queries=suggested_queries,
        agent_display_name=agent_display_name,
    )
    logger.info(
        "[DataAgentRunner] Clarification assembled scenario=%s skip_llm=%s quick_source=%s",
        scenario,
        skip_llm,
        quick_source,
    )
    return finalize_visible_reply(content, collapse_duplicates=False)


async def yield_contextual_clarification(
    runner: Any,
    *,
    user_question: str,
    history: List[Dict[str, str]],
    reasoning: str,
    missing_fields: tuple[str, ...] | None = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    yield {
        "type": "log",
        "id": f"clarify_{uuid.uuid4().hex[:8]}",
        "title": "需要补充查数信息",
        "details": reasoning,
        "status": "warning",
        "category": "intent",
    }
    content = await runner._generate_clarification_content(
        user_question=user_question,
        history=history,
        reasoning=reasoning,
        missing_fields=missing_fields,
    )
    yield {"content": content, "status": "success"}


async def generate_non_data_response(
    runner: Any,
    *,
    user_question: str,
) -> str:
    agent_display_name = _resolve_agent_display_name(runner)
    is_greeting = DataQueryPrompts._looks_like_greeting_or_capability_question(
        user_question, ""
    )
    llm_lead: str | None = None

    if is_greeting:
        try:
            llm = await AgentConfigProvider.get_configured_llm(
                streaming=False,
                config=runner.config,
            )
            chat_client = chat_client_from_handle(llm)
            raw_lead = await chat_client.generate_text(
                system_user_prompt_messages(
                    DataQueryPrompts.non_data_greeting_lead_generation_prompt(
                        agent_display_name=agent_display_name,
                        user_question=user_question,
                        agent_brief=_agent_brief(runner),
                    ),
                    user_prompt=user_question,
                )
            )
            if DataQueryPrompts.is_valid_non_data_greeting_lead(
                raw_lead,
                agent_display_name=agent_display_name,
            ):
                llm_lead = DataQueryPrompts.sanitize_clarification_lead(raw_lead)
            else:
                logger.info(
                    "[DataAgentRunner] Non-data greeting LLM lead rejected; using rule lead. agent=%s",
                    agent_display_name,
                )
        except Exception as e:
            logger.warning(
                "[DataAgentRunner] Non-data greeting lead generation failed: %s",
                e,
            )

    content = DataQueryPrompts.build_non_data_response(
        user_question,
        agent_display_name=agent_display_name,
        lead=llm_lead,
    )
    return finalize_visible_reply(content, collapse_duplicates=False)


async def yield_non_data_guidance(
    runner: Any,
    *,
    user_question: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    is_greeting = DataQueryPrompts._looks_like_greeting_or_capability_question(
        user_question, ""
    )
    if is_greeting:
        title = "友好回应并介绍能力"
        details = "识别为寒暄或能力咨询，先友好承接，再引导查数或切换智能体"
    else:
        title = "引导至更合适的智能体"
        details = "当前请求更偏通用问答，已提供切换入口与查数引导"
    yield {
        "type": "log",
        "id": f"non_data_{uuid.uuid4().hex[:8]}",
        "title": title,
        "details": details,
        "status": "info" if is_greeting else "warning",
        "category": "intent",
    }
    content = await generate_non_data_response(runner, user_question=user_question)
    yield {"content": content, "status": "success"}


async def yield_missing_reusable_result_clarification(
    runner: Any,
    history: List[Dict[str, str]],
    *,
    user_question: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    history_excerpt = DataQueryPrompts.format_clarification_history(history)
    reasoning = (
        "检测到本轮是基于上一轮结果的分析/可视化请求，"
        "但当前会话没有保存的结构化查询结果。"
    )
    yield {
        "type": "log",
        "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
        "title": "缺少可复用查询结果",
        "details": reasoning,
        "status": "error",
    }
    yield {
        "content": DataQueryPrompts.build_missing_reusable_result_fallback(
            history_excerpt,
            user_question=user_question,
        ),
        "status": "success",
    }
