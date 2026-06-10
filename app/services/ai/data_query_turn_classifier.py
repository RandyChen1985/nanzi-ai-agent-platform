"""ChatBI/DataQueryExecutor 专用轮次分类。

该模块只服务数据查询执行器。路由层只负责选择智能体；一旦进入
DataQueryExecutor，本模块负责判断本轮是新数据查询、复用上一轮结果，还是上下文动作。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from app.services.ai.intent_service import (
    IntentResponse,
    IntentType,
    looks_like_compound_query_with_viz,
    looks_like_context_action,
    looks_like_pure_result_followup,
    looks_like_skill_execution,
)
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from app.services.ai.turn_classifier import (
    load_last_data_result,
)

logger = logging.getLogger(__name__)


class DataQueryTurnType(str, Enum):
    NEW_DATA_QUERY = "new_data_query"
    REUSE_PREVIOUS_RESULT = "reuse_previous_result"
    CONTEXT_ACTION = "context_action"
    SKILL_EXECUTION = "skill_execution"


DATA_QUERY_TURN_TYPE_LABELS: dict[DataQueryTurnType, str] = {
    DataQueryTurnType.NEW_DATA_QUERY: "新数据查询",
    DataQueryTurnType.REUSE_PREVIOUS_RESULT: "复用上一轮结果",
    DataQueryTurnType.CONTEXT_ACTION: "上下文动作",
    DataQueryTurnType.SKILL_EXECUTION: "技能执行",
}


def data_query_turn_type_label(turn_type: DataQueryTurnType) -> str:
    return DATA_QUERY_TURN_TYPE_LABELS.get(turn_type, turn_type.value)


@dataclass
class DataQueryTurnClassification:
    turn_type: DataQueryTurnType
    reasoning: str
    requires_fresh_data: bool = True
    requires_few_shot: bool = True
    skip_intent_llm: bool = False
    intent: Optional[IntentType] = None


def _classification_for_turn_type(
    turn_type: DataQueryTurnType,
    reasoning: str,
    *,
    skip_intent_llm: bool,
) -> DataQueryTurnClassification:
    if turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type == DataQueryTurnType.CONTEXT_ACTION:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.GENERAL,
        )
    if turn_type == DataQueryTurnType.SKILL_EXECUTION:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    return DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning=reasoning,
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=skip_intent_llm,
        intent=IntentType.DATA_QUERY,
    )


def _looks_like_explicit_new_data_query(user_query: str) -> bool:
    """高置信的新查数请求，避免占用后续 Schema/SQL 编排 LLM 调用。"""
    q = (user_query or "").strip().lower()
    if not q:
        return False
    keywords = [
        "查询", "查一下", "查下", "统计", "列出", "列表", "筛选", "过滤",
        "最近", "今天", "昨天", "本周", "上周", "本月", "上月", "获取",
        "拉取", "明细", "记录", "趋势", "报表", "汇总", "对比", "多少",
        "用户表", "用户列表", "query", "how many", "count", "select ",
        "from ", "where ", "group by", "table", "users",
    ]
    return any(keyword in q for keyword in keywords)


def _recent_history_for_prompt(messages: Optional[List[Dict[str, str]]]) -> str:
    if not messages:
        return "无"
    lines = []
    for msg in messages[-6:]:
        role = msg.get("role") or ""
        if role not in ("user", "assistant"):
            continue
        content = re.sub(r"\s+", " ", str(msg.get("content") or "")).strip()
        if not content:
            continue
        if len(content) > 240:
            content = content[:240] + "..."
        role_name = "用户" if role == "user" else "助手"
        lines.append(f"{role_name}: {content}")
    return "\n".join(lines) if lines else "无"


def _parse_llm_json(content: str) -> Dict:
    text = (content or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except Exception:
            return {}


async def _classify_with_llm(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    has_last_data_result: bool,
) -> Optional[DataQueryTurnClassification]:
    prompt = f"""你是 ChatBI 数据查询执行器内部的请求类别分析器。

请结合最近对话、当前用户问题，以及是否存在上一轮结构化查询结果，判断当前请求属于以下哪一类：

1. new_data_query：需要重新查询业务数据，例如查询新的列表、指标、时间范围、筛选条件、对比维度，或同一句里要求查数并分析/可视化。
2. reuse_previous_result：不需要重新查库，只是基于上一轮结构化查询结果做展示、格式化、分析、总结、可视化、改列格式、排序说明等。
3. context_action：对已有上下文或上一轮结果执行保存、导出、发送、记住、沉淀为技能等动作。
4. skill_execution：显式要求使用/执行某个技能。

约束：
- 如果选择 reuse_previous_result，必须确认“存在上一轮结构化查询结果”为 true。
- 如果用户提出新的查询对象、时间范围、筛选条件或要求重新查数据，选择 new_data_query。
- 只返回 JSON，不要解释，不要 Markdown。

JSON 格式：
{{"turn_type":"new_data_query|reuse_previous_result|context_action|skill_execution","reasoning":"一句中文原因"}}

【存在上一轮结构化查询结果】
{str(has_last_data_result).lower()}

【最近对话】
{_recent_history_for_prompt(messages)}

【当前用户问题】
{user_query}
"""
    try:
        from app.services.ai.config import AgentConfigProvider

        llm = await AgentConfigProvider.get_configured_llm(streaming=False)
        chat_client = chat_client_from_handle(llm)
        content = await chat_client.generate_text(
            [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=prompt)],
                )
            ]
        )
        data = _parse_llm_json(content)
        raw_turn_type = str(data.get("turn_type") or "").strip()
        reasoning = str(data.get("reasoning") or "ChatBI 请求类别由大模型兜底识别").strip()
        turn_type = DataQueryTurnType(raw_turn_type)
        return _classification_for_turn_type(turn_type, reasoning, skip_intent_llm=False)
    except Exception as e:
        logger.warning("[DataQueryTurnClassifier] LLM fallback classification failed: %s", e)
        return None


async def resolve_data_query_turn_classification(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    user_info: Optional[Dict] = None,
    conversation_id: Optional[str] = None,
    has_last_data_result: Optional[bool] = None,
) -> tuple[DataQueryTurnClassification, IntentResponse, float]:
    """Resolve ChatBI-internal data-query request classification.

    Router/dispatcher may provide coarse context, but DataQueryExecutor treats this
    result as the final authority for its own execution path.
    """
    if has_last_data_result is None and conversation_id:
        has_last_data_result = await load_last_data_result(user_info, conversation_id) is not None
    has_last_data_result = bool(has_last_data_result)

    q = (user_query or "").strip()
    intent_start = time.time()
    classification = await _classify_with_llm(
        q,
        messages,
        has_last_data_result=has_last_data_result,
    )
    intent_elapsed_ms = (time.time() - intent_start) * 1000

    if classification is None and looks_like_context_action(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.CONTEXT_ACTION,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到对已有上下文/结果的动作（保存/导出/记住等）",
            requires_fresh_data=False,
            requires_few_shot=False,
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )
    elif classification is None and looks_like_skill_execution(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.SKILL_EXECUTION,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到显式技能执行请求",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif classification is None and looks_like_pure_result_followup(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
            reasoning=(
                "请求类别 LLM 未返回有效结果；规则兜底检测到对上一轮数据结果的追问，复用结果"
                if has_last_data_result
                else "请求类别 LLM 未返回有效结果；规则兜底检测到结果追问，但当前会话没有可复用结构化查询结果"
            ),
            requires_fresh_data=False,
            requires_few_shot=False,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif classification is None and looks_like_compound_query_with_viz(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到查数+可视化复合请求",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif classification is None and _looks_like_explicit_new_data_query(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到明确查数关键词，按新数据查询处理",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if classification is None:
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="请求类别 LLM 与规则兜底均未返回特殊类别，按新数据查询处理",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    intent_info = IntentResponse(
        intent=classification.intent or IntentType.DATA_QUERY,
        confidence=1.0 if classification.skip_intent_llm else 0.85,
        reasoning=classification.reasoning,
        entities=[],
    )
    return classification, intent_info, intent_elapsed_ms
