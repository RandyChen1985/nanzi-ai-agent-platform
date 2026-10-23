from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
from app.services.ai.turn_decision import TurnDecision


class KnowledgeExecutor(BaseExecutor):
    """知识库问答专用 Executor。"""

    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        execution_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Dict[str, Any] = None,
        turn_decision: Optional[TurnDecision] = None,
        current_user_query: Optional[str] = None,
    ):
        super().__init__(
            config,
            trace_id,
            trace_buffer,
            execution_options,
            user_info,
            conversation_id,
            permission_options,
        )
        self.turn_decision = turn_decision
        self.current_user_query = current_user_query

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        runner = KnowledgeAgentRunner(
            config=self.config,
            trace_id=self.trace_id,
            trace_buffer=self.trace_buffer,
            execution_options=self.execution_options,
            permission_options=self.permission_options,
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            turn_decision=self.turn_decision,
            current_user_query=self.current_user_query,
        )
        runner.step_counter = self.step_counter

        async for chunk in runner.execute(history):
            yield chunk

        self.step_counter = runner.step_counter
