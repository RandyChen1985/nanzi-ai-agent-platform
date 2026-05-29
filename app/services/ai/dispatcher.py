import logging
import json
import time
from typing import List, Dict, Any, Optional
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.intent_service import intent_service, IntentType, IntentResponse, looks_like_data_followup
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.chat_executor import GeneralChatExecutor
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
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None
    ) -> BaseExecutor:
        """
        Determines and returns the correct Executor instance.
        """
        
        # 1. External Engine Check
        if agent_config.engine_type == 'RAGFLOW':
            return RAGExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        
        if agent_config.engine_type == 'OPENCLAW':
            return OpenClawExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)

        # 2. Capability Check first — 只有具备 data_query 能力的智能体才需要意图识别。
        #    非数据智能体一律走 GeneralChatExecutor，提前返回可省掉一次昂贵且注定被丢弃的意图识别 LLM 调用。
        can_do_data = "data_query" in (agent_config.capabilities or [])
        if not can_do_data:
            return GeneralChatExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)

        # 3. 廉价短路：本轮明显是对上一轮数据结果的追问（可视化/分析等），且确实存在
        #    可复用的结构化结果时，直接走 DataQueryExecutor，省掉一次意图识别 LLM 调用。
        if conversation_id and looks_like_data_followup(user_query):
            last_data_result = await AgentDispatcher._load_last_data_result(user_info, conversation_id)
            if last_data_result:
                executor = DataQueryExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
                executor.intent_info = IntentResponse(
                    intent=IntentType.DATA_QUERY,
                    confidence=1.0,
                    reasoning="检测到对上一轮数据结果的追问，复用结果（启发式短路，跳过意图识别）",
                    entities=[],
                )
                executor.intent_elapsed_ms = 0.0
                return executor

        # 4. Intent Recognition（仅数据能力智能体）—— 传入最近对话，帮助识别省略主语的追问
        intent_start = time.time()
        prior_messages = messages[:-1] if messages else None
        intent_info = await intent_service.identify_intent(user_query, history=prior_messages)
        intent_elapsed_ms = (time.time() - intent_start) * 1000

        if intent_info.intent == IntentType.DATA_QUERY:
            executor = DataQueryExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
            executor.intent_info = intent_info  # Attach for potential use
            executor.intent_elapsed_ms = intent_elapsed_ms
            return executor

        # Fallback to General Chat
        return GeneralChatExecutor(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id)

    @staticmethod
    async def _load_last_data_result(
        user_info: Optional[Dict[str, Any]],
        conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """读取本会话最近一次结构化查询结果，用于追问短路判断。"""
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
            logger.warning(f"[Dispatcher] Failed to load last data result: {e}")
            return None
