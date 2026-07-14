from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.services.ai.runtime.agentscope.pending_store import (
    PENDING_CONFIRMATION_TTL_SECONDS,
    PendingAgentScopeSnapshot,
    pending_agentscope_store,
)


@dataclass
class PendingAgentScopeConfirmation:
    """In-process handle; cross-process resume uses serialized snapshot only."""

    request_id: str
    snapshot: PendingAgentScopeSnapshot
    agent: Any | None = None
    runner: Any | None = None
    tools: list[Any] = field(default_factory=list)
    native_model: Any | None = None
    tool_call: Any | None = None
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def reply_id(self) -> str:
        return self.snapshot.reply_id

    @property
    def trace_id(self) -> str:
        return self.snapshot.trace_id

    @property
    def user_id(self) -> str | None:
        return self.snapshot.user_id

    def is_expired(self, now: float | None = None) -> bool:
        return self.snapshot.is_expired(now)

    @classmethod
    def from_snapshot(cls, snapshot: PendingAgentScopeSnapshot) -> PendingAgentScopeConfirmation:
        tool_call = _deserialize_tool_call(snapshot.tool_call)
        return cls(
            request_id=snapshot.request_id,
            snapshot=snapshot,
            tool_call=tool_call,
            state=snapshot.stream_state,
        )


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump(mode="json")
    return {
        "id": getattr(tool_call, "id", ""),
        "name": getattr(tool_call, "name", ""),
        "input": getattr(tool_call, "input", ""),
    }


def _deserialize_tool_call(payload: dict[str, Any]) -> Any:
    try:
        from agentscope.message import ToolCallBlock

        return ToolCallBlock(**payload)
    except Exception:
        return payload


class PendingAgentScopeConfirmationRegistry:
    def __init__(self) -> None:
        self._items: dict[str, PendingAgentScopeConfirmation] = {}

    async def register(
        self,
        *,
        kind: str,
        agent: Any,
        runner: Any,
        tools: list[Any],
        native_model: Any,
        tool_call: Any,
        reply_id: str,
        trace_id: str,
        user_id: str | None = None,
        conversation_id: str | None = None,
        agent_name: str,
        state: dict[str, Any] | None = None,
        runner_context: dict[str, Any] | None = None,
    ) -> PendingAgentScopeConfirmation:
        await self.prune()
        request_id = pending_agentscope_store.new_request_id()
        evidence_ledger = getattr(runner, "_evidence_ledger", None)
        snapshot = PendingAgentScopeSnapshot(
            request_id=request_id,
            kind=kind,
            user_id=str(user_id) if user_id is not None else None,
            conversation_id=conversation_id,
            trace_id=trace_id,
            reply_id=reply_id,
            agent_name=agent_name,
            tool_call=_serialize_tool_call(tool_call),
            agent_state=agent.state.model_dump(mode="json"),
            stream_state=state or {},
            runner_context=runner_context or {},
            evidence_receipts=(
                evidence_ledger.to_snapshot()
                if evidence_ledger is not None
                else []
            ),
        )
        await pending_agentscope_store.register(snapshot)
        request = PendingAgentScopeConfirmation(
            request_id=request_id,
            snapshot=snapshot,
            agent=agent,
            runner=runner,
            tools=tools,
            native_model=native_model,
            tool_call=tool_call,
            state=state or {},
        )
        self._items[request_id] = request
        return request

    def pop(self, request_id: str) -> PendingAgentScopeConfirmation | None:
        """Sync pop for same-process tests; production uses pop_async."""
        self.prune_sync()
        request = self._items.pop(request_id, None)
        if request and request.is_expired():
            return None
        return request

    async def pop_async(
        self,
        request_id: str,
        *,
        user_id: str | int | None = None,
    ) -> PendingAgentScopeConfirmation | None:
        self.prune_sync()
        request = self._items.get(request_id)
        if request and not request.is_expired():
            if request.user_id and user_id is not None and str(request.user_id) != str(user_id):
                return None
            self._items.pop(request_id, None)
            await pending_agentscope_store.pop(request_id, user_id=user_id)
            return request
        if request:
            self._items.pop(request_id, None)

        snapshot = await pending_agentscope_store.pop(request_id, user_id=user_id)
        if not snapshot:
            return None
        return PendingAgentScopeConfirmation.from_snapshot(snapshot)

    def peek(self, request_id: str) -> PendingAgentScopeConfirmation | None:
        self.prune_sync()
        request = self._items.get(request_id)
        if request and request.is_expired():
            self._items.pop(request_id, None)
            return None
        return request

    def prune_sync(self) -> None:
        now = time.time()
        expired = [
            request_id
            for request_id, request in self._items.items()
            if request.is_expired(now)
        ]
        for request_id in expired:
            self._items.pop(request_id, None)

    async def prune(self) -> None:
        self.prune_sync()
        await pending_agentscope_store.prune()

    def clear(self) -> None:
        self._items.clear()
        pending_agentscope_store.clear()


pending_agentscope_confirmations = PendingAgentScopeConfirmationRegistry()
