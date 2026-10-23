from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List

from app.services.ai.executors.base import BaseExecutor
from app.services.ai.runners.data_agent_runner import DataAgentRunner
from app.services.ai.runtime.agentscope.data_runtime import DATA_QUERY_MAX_STEPS_CAP
from app.services.ai.turn_decision import TurnDecision


class DataQueryExecutor(BaseExecutor):
    """ChatBI/DataQuery executor backed by AgentScope native Agent + Toolkit."""

    def __init__(self, *args, **kwargs):
        self.turn_decision: TurnDecision | None = kwargs.pop("turn_decision", None)
        self.current_user_query: str | None = kwargs.pop("current_user_query", None)
        super().__init__(*args, **kwargs)
        self._runner: DataAgentRunner | None = None

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        runner = DataAgentRunner(
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
        async for chunk in runner.execute(history):
            yield chunk
        self.step_counter = runner.step_counter

    def resolve_has_data_output(self) -> bool:
        if self._runner is None:
            return False
        return self._runner.resolve_has_data_output()


__all__ = ["DATA_QUERY_MAX_STEPS_CAP", "DataQueryExecutor"]
