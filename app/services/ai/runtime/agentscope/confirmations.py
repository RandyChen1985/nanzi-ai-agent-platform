from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.services.ai.runtime.agentscope.pending_store import (
    PendingAgentScopeSnapshot,
    pending_agentscope_store,
)
from app.services.ai.runtime.agentscope.serialize import (
    resolve_awaitables,
    serialize_agent_state,
    serialize_jsonable,
)

# Backward-compatible aliases for existing tests / imports.
_resolve_snapshot_awaitables = resolve_awaitables
_serialize_agent_state = serialize_agent_state


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


async def _serialize_tool_call(
    tool_call: Any,
    *,
    awaitable_cache: dict[int, Any] | None = None,
) -> dict[str, Any]:
    if hasattr(tool_call, "model_dump"):
        if hasattr(tool_call, "suggested_rules"):
            suggested_rules = await resolve_awaitables(
                getattr(tool_call, "suggested_rules"),
                path="tool_call.suggested_rules",
                awaitable_cache=awaitable_cache,
            )
            # Keep the live AgentScope object JSON-safe as it is also held by
            # agent.state.context and may be persisted again after resume.
            try:
                tool_call.suggested_rules = suggested_rules or []
            except Exception:
                pass
        serialized = await serialize_jsonable(
            tool_call,
            path="tool_call",
            awaitable_cache=awaitable_cache,
        )
        if isinstance(serialized, dict):
            return serialized
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
        awaitable_cache: dict[int, Any] = {}
        serialized_tool_call = await _serialize_tool_call(
            tool_call,
            awaitable_cache=awaitable_cache,
        )
        serialized_agent_state = await serialize_agent_state(
            agent.state,
            awaitable_cache=awaitable_cache,
        )
        serialized_stream_state = await serialize_jsonable(
            state or {},
            path="stream_state",
            awaitable_cache=awaitable_cache,
        )
        serialized_runner_context = await serialize_jsonable(
            runner_context or {},
            path="runner_context",
            awaitable_cache=awaitable_cache,
        )
        if not isinstance(serialized_stream_state, dict):
            serialized_stream_state = {"value": serialized_stream_state}
        if not isinstance(serialized_runner_context, dict):
            serialized_runner_context = {"value": serialized_runner_context}
        snapshot = PendingAgentScopeSnapshot(
            request_id=request_id,
            kind=kind,
            user_id=str(user_id) if user_id is not None else None,
            conversation_id=conversation_id,
            trace_id=trace_id,
            reply_id=reply_id,
            agent_name=agent_name,
            tool_call=serialized_tool_call,
            agent_state=serialized_agent_state,
            stream_state=serialized_stream_state,
            runner_context=serialized_runner_context,
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

    async def peek_async(
        self,
        request_id: str,
        *,
        user_id: str | int | None = None,
    ) -> PendingAgentScopeConfirmation | None:
        """非破坏式读取：先查内存，再查 Redis 快照，均不删除。

        供恢复流程「先校验、后消费」使用：避免在配额/会话锁校验失败或用户取消时，
        快照已被 pop 删除导致确认结果丢失、无法重试。
        """
        self.prune_sync()
        request = self._items.get(request_id)
        if request and not request.is_expired():
            if request.user_id and user_id is not None and str(request.user_id) != str(user_id):
                return None
            return request
        if request:
            self._items.pop(request_id, None)

        snapshot = await pending_agentscope_store.peek(request_id, user_id=user_id)
        if not snapshot:
            return None
        return PendingAgentScopeConfirmation.from_snapshot(snapshot)

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
