"""会话级通用请求分类：供路由、General/Knowledge 执行器复用。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.services.ai.intent_service import (
    IntentResponse,
    IntentType,
    intent_service,
    looks_like_compound_query_with_viz,
    looks_like_context_action,
    looks_like_greeting,
    looks_like_knowledge_query,
    looks_like_meta_action,
    looks_like_pure_result_followup,
    looks_like_skill_execution,
    looks_like_web_search_query,
)

logger = logging.getLogger(__name__)


class TurnType(str, Enum):
    DATA_QUERY_REQUEST = "data_query_request"
    CONTEXT_ACTION = "context_action"
    SKILL_EXECUTION = "skill_execution"
    META_ACTION = "meta_action"
    GENERAL = "general"
    KNOWLEDGE = "knowledge"


TURN_TYPE_LABELS: dict[TurnType, str] = {
    TurnType.DATA_QUERY_REQUEST: "数据查询请求",
    TurnType.CONTEXT_ACTION: "上下文动作",
    TurnType.SKILL_EXECUTION: "技能执行",
    TurnType.META_ACTION: "元操作",
    TurnType.GENERAL: "通用助手",
    TurnType.KNOWLEDGE: "知识库问答",
}


def turn_type_label(turn_type: TurnType) -> str:
    return TURN_TYPE_LABELS.get(turn_type, turn_type.value)


@dataclass
class TurnClassification:
    turn_type: TurnType
    reasoning: str
    requires_knowledge_search: bool = False
    skip_intent_llm: bool = False
    intent: Optional[IntentType] = None
    knowledge_preemption_allowed: bool = False


def should_inject_ltm(turn_type: Optional[TurnType]) -> bool:
    """数据查询/技能执行类请求通常不需要 LTM 画像块。"""
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.DATA_QUERY_REQUEST,
        TurnType.SKILL_EXECUTION,
    )


def should_inject_memory_recall_hint(turn_type: Optional[TurnType]) -> bool:
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.DATA_QUERY_REQUEST,
        TurnType.SKILL_EXECUTION,
        TurnType.KNOWLEDGE,
    )


def should_run_active_memory_preload(turn_type: Optional[TurnType]) -> bool:
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.DATA_QUERY_REQUEST,
        TurnType.SKILL_EXECUTION,
    )


def should_inject_user_context(turn_type: Optional[TurnType]) -> bool:
    """数据查询/技能执行类请求可省略用户画像 system 注入，权限仍走 user_info。"""
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.DATA_QUERY_REQUEST,
        TurnType.SKILL_EXECUTION,
    )


def default_thought_expanded(turn_type: Optional[TurnType]) -> bool:
    """前端深度思考面板默认是否展开：数据查询请求默认展开。"""
    return turn_type == TurnType.DATA_QUERY_REQUEST


SharedTurn = Tuple[TurnClassification, Optional[IntentResponse], float]


def classify_turn_heuristic(
    user_query: str,
    *,
    can_do_data: bool,
    has_last_data_result: bool = False,
    knowledge_dataset_ids: Optional[List[str]] = None,
    agent_has_knowledge_binding: bool = False,
) -> Optional[TurnClassification]:
    """启发式分类；若无法确定则返回 None，需再调用意图 LLM。"""
    q = (user_query or "").strip()
    if not q:
        return None

    if looks_like_meta_action(q):
        return TurnClassification(
            turn_type=TurnType.META_ACTION,
            reasoning="检测到元操作（创建/保存技能等），无需查数",
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if looks_like_context_action(q):
        return TurnClassification(
            turn_type=TurnType.CONTEXT_ACTION,
            reasoning="检测到对已有上下文/结果的动作（保存/导出/记住等）",
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if looks_like_greeting(q):
        return TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning="检测到问候/寒暄（启发式短路，跳过意图识别）",
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if can_do_data and looks_like_skill_execution(q):
        return TurnClassification(
            turn_type=TurnType.SKILL_EXECUTION,
            reasoning="检测到显式技能执行请求",
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if can_do_data and looks_like_pure_result_followup(q) and has_last_data_result:
        return TurnClassification(
            turn_type=TurnType.DATA_QUERY_REQUEST,
            reasoning="检测到对上一轮数据结果的追问（启发式短路，跳过意图识别）",
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if knowledge_dataset_ids or agent_has_knowledge_binding:
        return TurnClassification(
            turn_type=TurnType.KNOWLEDGE,
            reasoning=(
                "会话已绑定知识库（dataset_ids 或智能体知识库配置），按知识库问答处理"
            ),
            requires_knowledge_search=True,
            skip_intent_llm=True,
            intent=IntentType.KNOWLEDGE_BASE,
            knowledge_preemption_allowed=True,
        )

    # 联网/外部搜索：未绑定内部知识库时，交给通用助手（含 web_search 工具），
    # 避免被当成知识库问答而因缺少 dataset 被终止。
    if looks_like_web_search_query(q):
        return TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning="检测到联网/外部公网搜索请求，交由通用助手联网检索（启发式短路）",
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if looks_like_knowledge_query(q):
        return TurnClassification(
            turn_type=TurnType.KNOWLEDGE,
            reasoning="检测到知识库/SOP 类问法（启发式短路，跳过意图识别）",
            requires_knowledge_search=True,
            skip_intent_llm=True,
            intent=IntentType.KNOWLEDGE_BASE,
        )

    if can_do_data and looks_like_compound_query_with_viz(q):
        return TurnClassification(
            turn_type=TurnType.DATA_QUERY_REQUEST,
            reasoning="检测到查数+可视化复合请求（启发式短路，跳过意图识别）",
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    return None


def classify_turn_from_intent(
    intent_info: IntentResponse,
    *,
    can_do_data: bool,
    user_query: str = "",
    has_last_data_result: bool = False,
    has_knowledge_binding: bool = False,
) -> TurnClassification:
    """将意图 LLM 结果映射为统一轮次分类。"""
    if can_do_data and intent_info.intent == IntentType.DATA_QUERY:
        is_followup_semantics = False
        try:
            from app.services.ai.intent_service import _FOLLOWUP_KEYWORDS, _NEW_QUERY_KEYWORDS
            q_lower = (user_query or "").lower()
            has_new_query_signal = any(w in q_lower for w in _NEW_QUERY_KEYWORDS)
            is_followup_semantics = (
                not has_new_query_signal
                and (
                    "追问" in (intent_info.reasoning or "")
                    or "可视化" in (intent_info.reasoning or "")
                    or "上一轮" in (intent_info.reasoning or "")
                    or "分析" in (intent_info.reasoning or "")
                    or "图表" in (intent_info.reasoning or "")
                    or any(w in q_lower for w in _FOLLOWUP_KEYWORDS)
                )
            )
        except Exception:
            pass

        if has_last_data_result and is_followup_semantics:
            return TurnClassification(
                turn_type=TurnType.DATA_QUERY_REQUEST,
                reasoning=f"意图大模型识别为查数大类，检测到缓存数据且符合广义追问语义: {intent_info.reasoning}",
                skip_intent_llm=False,
                intent=IntentType.DATA_QUERY,
            )

        return TurnClassification(
            turn_type=TurnType.DATA_QUERY_REQUEST,
            reasoning=intent_info.reasoning,
            skip_intent_llm=False,
            intent=IntentType.DATA_QUERY,
        )

    if intent_info.intent in (IntentType.KNOWLEDGE_BASE, IntentType.UNKNOWN):
        reasoning = intent_info.reasoning or ""
        # 未绑定内部知识库时，若本轮其实是联网/外部搜索，纠正为通用助手处理，
        # 避免知识库 executor 因缺少 dataset 直接终止。
        if not has_knowledge_binding and looks_like_web_search_query(user_query):
            return TurnClassification(
                turn_type=TurnType.GENERAL,
                reasoning=(
                    f"{reasoning}（识别为联网/外部搜索，未绑定内部知识库，改由通用助手联网检索）"
                ),
                skip_intent_llm=False,
                intent=IntentType.GENERAL,
            )
        if (
            intent_info.intent == IntentType.KNOWLEDGE_BASE
            or "search_knowledge" in reasoning
            or "知识库" in reasoning
        ):
            return TurnClassification(
                turn_type=TurnType.KNOWLEDGE,
                reasoning=reasoning or "识别为知识库问答",
                requires_knowledge_search=True,
                skip_intent_llm=False,
                intent=IntentType.KNOWLEDGE_BASE,
                knowledge_preemption_allowed=has_knowledge_binding,
            )

    if not can_do_data and intent_info.intent == IntentType.DATA_QUERY:
        return TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning=(
                f"{intent_info.reasoning}（当前 Agent 无 data_query 能力，按通用对话处理）"
            ),
            skip_intent_llm=False,
            intent=IntentType.DATA_QUERY,
        )

    return TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning=intent_info.reasoning,
        skip_intent_llm=False,
        intent=intent_info.intent,
    )


def attach_turn_classification(
    executor,
    classification: TurnClassification,
    *,
    intent_info: Optional[IntentResponse] = None,
    intent_elapsed_ms: float = 0.0,
):
    """把通用分类结果挂到 Executor 上，供日志与非 ChatBI 执行策略使用。"""
    executor.turn_classification = classification
    executor.intent_elapsed_ms = intent_elapsed_ms

    if intent_info is not None:
        executor.intent_info = intent_info
    elif classification.intent is not None:
        executor.intent_info = IntentResponse(
            intent=classification.intent,
            confidence=1.0 if classification.skip_intent_llm else 0.0,
            reasoning=classification.reasoning,
            entities=[],
        )

    return executor


async def load_last_data_result(
    user_info: Optional[Dict[str, Any]],
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """读取本会话最近一次结构化查询结果。"""
    if not user_info:
        return None
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    if not raw_user_id:
        return None
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None
    try:
        from app.services.ai.memory_service import memory_service

        return await memory_service.get_last_data_result(user_id, conversation_id)
    except Exception as e:
        logger.warning("[TurnClassifier] Failed to load last data result: %s", e)
        return None


async def resolve_turn_classification(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    can_do_data: bool,
    user_info: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    knowledge_dataset_ids: Optional[List[str]] = None,
    agent_has_knowledge_binding: bool = False,
) -> Tuple[TurnClassification, Optional[IntentResponse], float]:
    """启发式 + 意图 LLM 的统一分类入口（Dispatcher 使用）。"""
    has_last_data_result = False
    if conversation_id and can_do_data:
        has_last_data_result = await load_last_data_result(user_info, conversation_id) is not None

    classification = classify_turn_heuristic(
        user_query,
        can_do_data=can_do_data,
        has_last_data_result=has_last_data_result,
        knowledge_dataset_ids=knowledge_dataset_ids,
        agent_has_knowledge_binding=agent_has_knowledge_binding,
    )

    intent_info = None
    intent_elapsed_ms = 0.0
    if classification is None:
        intent_start = time.time()
        prior_messages = messages[:-1] if messages else None
        intent_info = await intent_service.identify_intent(user_query, history=prior_messages)
        intent_elapsed_ms = (time.time() - intent_start) * 1000
        has_knowledge_binding = bool(knowledge_dataset_ids or agent_has_knowledge_binding)
        classification = classify_turn_from_intent(
            intent_info,
            can_do_data=can_do_data,
            user_query=user_query,
            has_last_data_result=has_last_data_result,
            has_knowledge_binding=has_knowledge_binding,
        )

    return classification, intent_info, intent_elapsed_ms


def adapt_classification_for_agent(
    classification: TurnClassification,
    *,
    can_do_data: bool,
) -> TurnClassification:
    """多智能体场景：同一轮分类结果按各 Agent 能力适配 Executor 策略。"""
    knowledge = (
        classification.requires_knowledge_search
        or classification.turn_type == TurnType.KNOWLEDGE
    )
    return TurnClassification(
        turn_type=classification.turn_type,
        reasoning=classification.reasoning,
        requires_knowledge_search=knowledge,
        skip_intent_llm=classification.skip_intent_llm,
        intent=classification.intent,
        knowledge_preemption_allowed=classification.knowledge_preemption_allowed,
    )


async def resolve_turn_for_session(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    can_do_data: bool,
    user_info: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    knowledge_dataset_ids: Optional[List[str]] = None,
    agent_has_knowledge_binding: bool = False,
) -> SharedTurn:
    """AgentService 统一入口：启发式优先，判不准则调用意图 LLM。"""
    return await resolve_turn_classification(
        user_query,
        messages,
        can_do_data=can_do_data,
        user_info=user_info,
        conversation_id=conversation_id,
        knowledge_dataset_ids=knowledge_dataset_ids,
        agent_has_knowledge_binding=agent_has_knowledge_binding,
    )
