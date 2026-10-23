import logging
from typing import List, Dict, Any, Optional
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.assistant_executor import AssistantExecutor
from app.services.ai.executors.knowledge_executor import KnowledgeExecutor
from app.services.ai.executors.rag_executor import RAGExecutor
from app.services.ai.executors.openclaw_executor import OpenClawExecutor
from app.services.ai.agent_readiness import has_knowledge_binding
from app.services.ai.turn_decision import TurnDecision

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
        execution_options: Optional[Dict[str, Any]] = None,
        permission_options: Optional[Dict[str, Any]] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        turn_decision: Optional[TurnDecision] = None,
    ) -> BaseExecutor:
        """
        Determine and return the executor selected by the resolved turn decision.

        The dispatcher does not classify a turn and does not recover routing data
        from an untyped dictionary. RouterService owns the outer decision; the
        ChatBI executor owns its internal data-query classification.
        """

        if turn_decision is None or turn_decision.route_status != "resolved":
            raise ValueError("AgentDispatcher requires a resolved TurnDecision")

        # 1. External Engine Check
        if agent_config.engine_type == 'RAGFLOW':
            return RAGExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                execution_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
                current_user_query=user_query,
            )

        if agent_config.engine_type == 'OPENCLAW':
            return OpenClawExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                execution_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
                current_user_query=user_query,
            )

        can_do_data = "data_query" in (agent_config.capabilities or [])
        agent_has_knowledge_binding = has_knowledge_binding(
            capabilities=agent_config.capabilities,
            engine_config=agent_config.engine_config,
            tools=agent_config.tools,
            agent_type=agent_config.agent_type,
        )

        if turn_decision.turn_kind == "knowledge":
            explicit_knowledge_route = turn_decision.user_action_type == "ask_knowledge"
            has_search_tool = "search_knowledge_base" in {
                getattr(tool, "name", tool) for tool in agent_config.tools or []
            }
            knowledge_available = agent_has_knowledge_binding or (
                explicit_knowledge_route
                and (
                    can_do_data
                    or (
                        "knowledge_base" in (agent_config.capabilities or [])
                        and has_search_tool
                    )
                )
            )
            if knowledge_available:
                logger.info(
                    "[Dispatcher] turn=knowledge executor=Knowledge agent=%s",
                    agent_config.agent_name,
                )
                return KnowledgeExecutor(
                    agent_config,
                    trace_id,
                    trace_buffer,
                    execution_options,
                    user_info,
                    conversation_id,
                    permission_options=permission_options,
                    turn_decision=turn_decision,
                    current_user_query=user_query,
                )
            logger.info(
                "[Dispatcher] knowledge route unavailable; fallback=Assistant "
                "agent=%s capabilities=%s tools=%s",
                agent_config.agent_name,
                agent_config.capabilities or [],
                [getattr(tool, "name", tool) for tool in agent_config.tools or []],
            )

        if turn_decision.turn_kind == "data_query" and can_do_data and (
            turn_decision.capability == "data_query"
            and turn_decision.allows_data_route
        ):
            logger.info(
                "[Dispatcher] turn=%s executor=DataQuery agent=%s (data_query capability)",
                turn_decision.turn_kind,
                agent_config.agent_name,
            )
            return DataQueryExecutor(
                agent_config,
                trace_id,
                trace_buffer,
                execution_options,
                user_info,
                conversation_id,
                permission_options=permission_options,
                turn_decision=turn_decision,
                current_user_query=user_query,
            )

        logger.info(
            "[Dispatcher] turn=%s executor=Assistant skip_intent=%s agent=%s",
            turn_decision.turn_kind,
            True,
            agent_config.agent_name,
        )

        return AssistantExecutor(
            agent_config,
            trace_id,
            trace_buffer,
            execution_options,
            user_info,
            conversation_id,
            permission_options=permission_options,
            turn_decision=turn_decision,
            current_user_query=user_query,
        )
