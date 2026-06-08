from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.runners.general_agent_runner import GeneralAgentRunner


class GeneralChatExecutor(BaseExecutor):
    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        route_hints: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self.turn_classification = None
        self._requires_knowledge_search = False
        self.route_hints = route_hints or {}

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        runner = GeneralAgentRunner(
            config=self.config,
            trace_id=self.trace_id,
            trace_buffer=self.trace_buffer,
            debug_options=self.debug_options,
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            route_hints=self.route_hints,
        )
        runner.intent_info = self.intent_info
        runner.intent_elapsed_ms = self.intent_elapsed_ms
        runner.turn_classification = self.turn_classification
        runner._requires_knowledge_search = self._requires_knowledge_search
        runner.step_counter = self.step_counter

        async for chunk in runner.execute(history):
            yield chunk

        self.step_counter = runner.step_counter
        self.intent_info = runner.intent_info
        self.intent_elapsed_ms = runner.intent_elapsed_ms
        self.turn_classification = runner.turn_classification
        self._requires_knowledge_search = runner._requires_knowledge_search
