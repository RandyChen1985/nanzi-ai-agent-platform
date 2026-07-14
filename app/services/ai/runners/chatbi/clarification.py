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


async def yield_non_data_guidance(
    *,
    user_question: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    yield {
        "type": "log",
        "id": f"non_data_{uuid.uuid4().hex[:8]}",
        "title": "当前问题更适合其他智能体",
        "details": "当前请求不属于业务数据查询，已提供智能体切换入口",
        "status": "warning",
        "category": "intent",
    }
    yield {
        "content": DataQueryPrompts.build_non_data_response(user_question),
        "status": "success",
    }


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
