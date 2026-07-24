from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.runners.data_agent_runner import DataAgentRunner
from app.services.ai.runtime.agentscope.data_runtime import DATA_QUERY_MAX_STEPS_CAP


class DataQueryExecutor(BaseExecutor):
    """ChatBI/DataQuery executor backed by AgentScope native Agent + Toolkit."""

    def __init__(self, *args, **kwargs):
        self.route_hints = kwargs.pop("route_hints", None) or {}
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
            debug_options=self.debug_options,
            permission_options=self.permission_options,
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            route_hints=self.route_hints,
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
