import logging
from typing import List, Dict, Any, Optional
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.turn_classifier import (
    SharedTurn,
    TurnType,
    adapt_classification_for_agent,
    attach_turn_classification,
    resolve_turn_for_session,
    turn_type_label,
)
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.assistant_executor import AssistantExecutor
from app.services.ai.executors.knowledge_executor import KnowledgeExecutor
from app.services.ai.executors.rag_executor import RAGExecutor
from app.services.ai.executors.openclaw_executor import OpenClawExecutor

logger = logging.getLogger(__name__)

class AgentDispatcher:
    """
    Dispatches agent execution to the appropriate Executor based on configuration and intent.
    """

    @staticmethod
    async def dispatch(
        agent_config: ChatConfig,
        user_query: str,
        messages: List[Dict[str, str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Optional[Dict[str, Any]] = None,
        permission_options: Optional[Dict[str, Any]] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        shared_turn: Optional[SharedTurn] = None,
        route_hints: Optional[Dict[str, Any]] = None,
    ) -> BaseExecutor:
        """
        Determines and returns the correct Executor instance.
        shared_turn: 多智能体/会话级已算好的分类，避免重复意图 LLM。
        """

        # 1. External Engine Check
        if agent_config.engine_type == 'RAGFLOW':
            return RAGExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                debug_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
            )

        if agent_config.engine_type == 'OPENCLAW':
            return OpenClawExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                debug_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
            )

        can_do_data = "data_query" in (agent_config.capabilities or [])

        # 2. 会话级轮次分类：知识库问答优先于 ChatBI / General 分流
        if shared_turn is not None:
            classification, intent_info, intent_elapsed_ms = shared_turn
            classification = adapt_classification_for_agent(classification, can_do_data=can_do_data)
        else:
            classification, intent_info, intent_elapsed_ms = await resolve_turn_for_session(
                user_query,
                messages,
                can_do_data=can_do_data,
                user_info=user_info,
                conversation_id=conversation_id,
            )
            classification = adapt_classification_for_agent(classification, can_do_data=can_do_data)

        if classification.turn_type == TurnType.KNOWLEDGE:
            logger.info(
                "[Dispatcher] turn=%s executor=Knowledge skip_intent=%s agent=%s",
                turn_type_label(classification.turn_type),
                classification.skip_intent_llm,
                agent_config.agent_name,
            )
            executor = KnowledgeExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                debug_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
            )
            attach_turn_classification(
                executor,
                classification,
                intent_info=intent_info,
                intent_elapsed_ms=intent_elapsed_ms,
            )
            return executor

        if can_do_data:
            logger.info(
                "[Dispatcher] turn=%s executor=DataQuery agent=%s (data_query capability)",
                turn_type_label(classification.turn_type),
                agent_config.agent_name,
            )
            return DataQueryExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                debug_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
            )

        logger.info(
            "[Dispatcher] turn=%s executor=Assistant skip_intent=%s agent=%s",
            turn_type_label(classification.turn_type),
            classification.skip_intent_llm,
            agent_config.agent_name,
        )

        executor = AssistantExecutor(
            agent_config,
            trace_id,
            trace_buffer,
            debug_options,
            user_info,
            conversation_id,
            permission_options=permission_options,
            route_hints=route_hints,
        )

        attach_turn_classification(
            executor,
            classification,
            intent_info=intent_info,
            intent_elapsed_ms=intent_elapsed_ms,
        )
        return executor
