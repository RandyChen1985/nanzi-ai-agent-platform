import pytest

from app.services.ai.agent_service import AgentService
from app.services.ai.runtime.agentscope.pending_store import PendingAgentScopeSnapshot

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_external_resume_rejects_permission_pending(monkeypatch):
    from app.services.ai.runtime.agentscope.confirmations import (
        PendingAgentScopeConfirmation,
        pending_agentscope_confirmations,
    )

    pending_agentscope_confirmations.clear()
    snapshot = PendingAgentScopeSnapshot(
        request_id="perm_only",
        kind="permission",
        user_id="u1",
        conversation_id="c1",
        trace_id="trace-1",
        reply_id="reply-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "bash", "input": "{}"},
        agent_state={},
        stream_state={},
        runner_context={"runner_type": "general", "config": {}},
    )
    pending_agentscope_confirmations._items["perm_only"] = PendingAgentScopeConfirmation(
        request_id="perm_only",
        snapshot=snapshot,
        tool_call=snapshot.tool_call,
    )

    async def _pop(request_id, user_id=None):
        return pending_agentscope_confirmations._items.pop(request_id, None)

    monkeypatch.setattr(pending_agentscope_confirmations, "pop_async", _pop)

    events = []
    async for chunk in AgentService().resume_agentscope_external_execution_stream(
        external_execution_request_id="perm_only",
        results=[{"id": "call_1", "name": "bash", "output": "ok"}],
        user_info={"user_id": "u1"},
    ):
        events.append(chunk)

    assert events[0]["status"] == "error"
    assert "permission" in events[0]["content"]
