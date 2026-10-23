from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.turn_decision import TurnDecision


class AssistantExecutor(BaseExecutor):
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
        super().__init__(config, trace_id, trace_buffer, execution_options, user_info, conversation_id, permission_options)
        self.turn_decision = turn_decision
        self.current_user_query = current_user_query
        self._runner: Optional[AssistantAgentRunner] = None

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        runner = AssistantAgentRunner(
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
        self._runner = runner
        runner.step_counter = self.step_counter

        async for chunk in runner.execute(history):
            yield chunk

        self.step_counter = runner.step_counter

    def resolve_has_tool_meta(self) -> bool:
        """A 项：本轮是否有待跨轮持久化的工具元数据。"""
        if self._runner is None:
            return False
        return self._runner.resolve_has_tool_meta()

    def resolve_tool_run_text(self, *, max_total_chars: int = 4000) -> str:
        """A 项：本轮工具调用转录文本（供保存点持久化）。"""
        if self._runner is None:
            return ""
        return self._runner.resolve_tool_run_text(max_total_chars=max_total_chars)
