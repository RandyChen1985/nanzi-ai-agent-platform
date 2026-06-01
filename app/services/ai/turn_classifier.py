"""统一轮次分类：收敛 Dispatcher / Intent / DataExecutor 分散的 K1/K2/K3 判定。"""
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
    looks_like_knowledge_query,
    looks_like_meta_action,
    looks_like_pure_result_followup,
    looks_like_skill_execution,
)

logger = logging.getLogger(__name__)


class TurnType(str, Enum):
    K1_NEW_QUERY = "k1_new_query"
    K2_REUSE_RESULT = "k2_reuse_result"
    K3_CONTEXT_ACTION = "k3_context_action"
    SKILL_EXECUTION = "skill_execution"
    META_ACTION = "meta_action"
    GENERAL = "general"
    KNOWLEDGE = "knowledge"


TURN_TYPE_LABELS: dict[TurnType, str] = {
    TurnType.K1_NEW_QUERY: "K1 新查数",
    TurnType.K2_REUSE_RESULT: "K2 复用结果",
    TurnType.K3_CONTEXT_ACTION: "K3 上下文动作",
    TurnType.SKILL_EXECUTION: "技能执行",
    TurnType.META_ACTION: "元操作",
    TurnType.GENERAL: "通用对话",
    TurnType.KNOWLEDGE: "知识库问答",
}


def turn_type_label(turn_type: TurnType) -> str:
    return TURN_TYPE_LABELS.get(turn_type, turn_type.value)


@dataclass
class TurnClassification:
    turn_type: TurnType
    reasoning: str
    requires_fresh_data: bool = True
    requires_few_shot: bool = True
    requires_knowledge_search: bool = False
    use_data_executor: bool = False
    skip_intent_llm: bool = False
    intent: Optional[IntentType] = None


def should_inject_ltm(turn_type: Optional[TurnType]) -> bool:
    """ChatBI 查数轮次通常不需要 LTM 画像块。"""
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.K1_NEW_QUERY,
        TurnType.K2_REUSE_RESULT,
        TurnType.SKILL_EXECUTION,
    )


def should_inject_memory_recall_hint(turn_type: Optional[TurnType]) -> bool:
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.K1_NEW_QUERY,
        TurnType.K2_REUSE_RESULT,
        TurnType.SKILL_EXECUTION,
        TurnType.KNOWLEDGE,
    )


def should_run_active_memory_preload(turn_type: Optional[TurnType]) -> bool:
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.K1_NEW_QUERY,
        TurnType.K2_REUSE_RESULT,
        TurnType.SKILL_EXECUTION,
    )


def should_inject_user_context(turn_type: Optional[TurnType]) -> bool:
    """纯查数/技能执行轮次可省略用户画像 system 注入，权限仍走 user_info。"""
    if turn_type is None:
        return True
    return turn_type not in (
        TurnType.K1_NEW_QUERY,
        TurnType.K2_REUSE_RESULT,
        TurnType.SKILL_EXECUTION,
    )


def default_thought_expanded(turn_type: Optional[TurnType]) -> bool:
    """前端深度思考面板默认是否展开：仅 K1 默认展开。"""
    return turn_type == TurnType.K1_NEW_QUERY


SharedTurn = Tuple[TurnClassification, Optional[IntentResponse], float]


def classify_turn_heuristic(
    user_query: str,
    *,
    can_do_data: bool,
    has_last_data_result: bool = False,
) -> Optional[TurnClassification]:
    """启发式分类；若无法确定则返回 None，需再调用意图 LLM。"""
    q = (user_query or "").strip()
    if not q:
        return None

    if looks_like_meta_action(q):
        return TurnClassification(
            turn_type=TurnType.META_ACTION,
            reasoning="检测到元操作（创建/保存技能等），无需查数",
            requires_fresh_data=False,
            requires_few_shot=False,
            use_data_executor=False,
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if looks_like_context_action(q):
        return TurnClassification(
            turn_type=TurnType.K3_CONTEXT_ACTION,
            reasoning="检测到对已有上下文/结果的动作（保存/导出/记住等）",
            requires_fresh_data=False,
            requires_few_shot=False,
            use_data_executor=False,
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )

    if can_do_data and looks_like_skill_execution(q):
        return TurnClassification(
            turn_type=TurnType.SKILL_EXECUTION,
            reasoning="检测到显式技能执行请求",
            requires_fresh_data=True,
            requires_few_shot=True,
            use_data_executor=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if can_do_data and looks_like_pure_result_followup(q) and has_last_data_result:
        return TurnClassification(
            turn_type=TurnType.K2_REUSE_RESULT,
            reasoning="检测到对上一轮数据结果的追问，复用结果（启发式短路，跳过意图识别）",
            requires_fresh_data=False,
            requires_few_shot=False,
            use_data_executor=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if looks_like_knowledge_query(q):
        return TurnClassification(
            turn_type=TurnType.KNOWLEDGE,
            reasoning="检测到知识库/SOP 类问法（启发式短路，跳过意图识别）",
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_knowledge_search=True,
            use_data_executor=False,
            skip_intent_llm=True,
            intent=IntentType.KNOWLEDGE_BASE,
        )

    if can_do_data and looks_like_compound_query_with_viz(q):
        return TurnClassification(
            turn_type=TurnType.K1_NEW_QUERY,
            reasoning="检测到查数+可视化复合请求（启发式短路，跳过意图识别）",
            requires_fresh_data=True,
            requires_few_shot=True,
            use_data_executor=True,
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
) -> TurnClassification:
    """将意图 LLM 结果映射为统一轮次分类。"""
    if can_do_data and intent_info.intent == IntentType.DATA_QUERY:
        is_followup_semantics = False
        try:
            from app.services.ai.intent_service import _FOLLOWUP_KEYWORDS
            is_followup_semantics = (
                "追问" in (intent_info.reasoning or "")
                or "可视化" in (intent_info.reasoning or "")
                or "上一轮" in (intent_info.reasoning or "")
                or "分析" in (intent_info.reasoning or "")
                or "图表" in (intent_info.reasoning or "")
                or any(w in (user_query or "").lower() for w in _FOLLOWUP_KEYWORDS)
            )
        except Exception:
            pass

        if has_last_data_result and is_followup_semantics:
            return TurnClassification(
                turn_type=TurnType.K2_REUSE_RESULT,
                reasoning=f"意图大模型识别为查数大类，检测到缓存数据且符合广义追问语义，无缝升级为 K2: {intent_info.reasoning}",
                requires_fresh_data=False,
                requires_few_shot=False,
                use_data_executor=True,
                skip_intent_llm=False,
                intent=IntentType.DATA_QUERY,
            )

        return TurnClassification(
            turn_type=TurnType.K1_NEW_QUERY,
            reasoning=intent_info.reasoning,
            requires_fresh_data=True,
            requires_few_shot=True,
            use_data_executor=True,
            skip_intent_llm=False,
            intent=IntentType.DATA_QUERY,
        )

    if intent_info.intent == IntentType.KNOWLEDGE_BASE:
        return TurnClassification(
            turn_type=TurnType.KNOWLEDGE,
            reasoning=intent_info.reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_knowledge_search=True,
            use_data_executor=False,
            skip_intent_llm=False,
            intent=IntentType.KNOWLEDGE_BASE,
        )

    return TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning=intent_info.reasoning,
        requires_fresh_data=False,
        requires_few_shot=False,
        use_data_executor=False,
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
    """把分类结果挂到 Executor 上，供 AgentService 日志与 DataExecutor 策略使用。"""
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

    if hasattr(executor, "_requires_fresh_data"):
        executor._requires_fresh_data = classification.requires_fresh_data
    if hasattr(executor, "_skip_few_shot"):
        executor._skip_few_shot = not classification.requires_few_shot
    if hasattr(executor, "_requires_knowledge_search"):
        executor._requires_knowledge_search = classification.requires_knowledge_search

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
) -> Tuple[TurnClassification, Optional[IntentResponse], float]:
    """启发式 + 意图 LLM 的统一分类入口（Dispatcher 使用）。"""
    has_last_data_result = False
    if conversation_id and can_do_data:
        has_last_data_result = await load_last_data_result(user_info, conversation_id) is not None

    classification = classify_turn_heuristic(
        user_query,
        can_do_data=can_do_data,
        has_last_data_result=has_last_data_result,
    )

    intent_info = None
    intent_elapsed_ms = 0.0
    if classification is None:
        intent_start = time.time()
        prior_messages = messages[:-1] if messages else None
        intent_info = await intent_service.identify_intent(user_query, history=prior_messages)
        intent_elapsed_ms = (time.time() - intent_start) * 1000
        classification = classify_turn_from_intent(
            intent_info,
            can_do_data=can_do_data,
            user_query=user_query,
            has_last_data_result=has_last_data_result,
        )

    return classification, intent_info, intent_elapsed_ms


def adapt_classification_for_agent(
    classification: TurnClassification,
    *,
    can_do_data: bool,
) -> TurnClassification:
    """多智能体场景：同一轮分类结果按各 Agent 能力适配 Executor 策略。"""
    if can_do_data and classification.use_data_executor:
        return classification

    knowledge = (
        classification.requires_knowledge_search
        or classification.turn_type == TurnType.KNOWLEDGE
    )
    return TurnClassification(
        turn_type=classification.turn_type,
        reasoning=classification.reasoning,
        requires_fresh_data=False,
        requires_few_shot=False,
        requires_knowledge_search=knowledge,
        use_data_executor=False,
        skip_intent_llm=classification.skip_intent_llm,
        intent=classification.intent,
    )


async def resolve_turn_for_session(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    can_do_data: bool,
    user_info: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> SharedTurn:
    """AgentService 统一入口：查数 Agent 走完整分类，其余 Agent 启发式优先。"""
    if can_do_data:
        return await resolve_turn_classification(
            user_query,
            messages,
            can_do_data=True,
            user_info=user_info,
            conversation_id=conversation_id,
        )

    classification = classify_turn_heuristic(
        user_query,
        can_do_data=False,
        has_last_data_result=False,
    )
    if classification is None:
        classification = TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning="通用对话（非数据智能体，跳过意图识别）",
            requires_fresh_data=False,
            requires_few_shot=False,
            use_data_executor=False,
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )
    return classification, None, 0.0
