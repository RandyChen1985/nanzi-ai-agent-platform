import json
import time
from types import SimpleNamespace

import pytest

from app.services.ai.runtime.agentscope.pending_store import (
    PENDING_CONFIRMATION_TTL_SECONDS,
    PendingAgentScopeSnapshot,
    PendingAgentScopeStore,
)
from app.services.ai.runtime.agentscope.state_store import (
    SCHEMA_VERSION,
    AgentStateStore,
    RuntimeStateEnvelope,
)
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runtime.agentscope.confirmations import (
    PendingAgentScopeConfirmationRegistry,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)


@pytest.mark.asyncio
async def test_pending_store_memory_roundtrip():
    store = PendingAgentScopeStore()
    store.clear()
    snapshot = PendingAgentScopeSnapshot(
        request_id="perm_test_1",
        kind="permission",
        user_id="u1",
        conversation_id="c1",
        trace_id="trace-1",
        reply_id="reply-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "test_tool", "input": "{}"},
        agent_state={"session_id": "s1", "context": []},
        stream_state={"user_query": "hello"},
        runner_context={"max_steps": 5},
        evidence_receipts=[{"call_id": "call-evidence-1"}],
    )
    await store.register(snapshot)
    loaded = await store.peek("perm_test_1", user_id="u1")
    assert loaded is not None
    assert loaded.request_id == "perm_test_1"
    assert loaded.evidence_receipts == [{"call_id": "call-evidence-1"}]

    popped = await store.pop("perm_test_1", user_id="u1")
    assert popped is not None
    assert popped.kind == "permission"
    assert await store.peek("perm_test_1", user_id="u1") is None


@pytest.mark.asyncio
async def test_confirmation_snapshot_captures_runner_evidence_ledger():
    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    ledger.record_success(
        call_id="call-evidence-1",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"train": "G1"},
    )
    runner = SimpleNamespace(_evidence_ledger=ledger)
    agent = SimpleNamespace(
        state=SimpleNamespace(model_dump=lambda mode: {"context": []})
    )
    registry = PendingAgentScopeConfirmationRegistry()

    pending = await registry.register(
        kind="permission",
        agent=agent,
        runner=runner,
        tools=[],
        native_model=SimpleNamespace(),
        tool_call={"id": "call-2", "name": "next-tool", "input": {}},
        reply_id="reply-1",
        trace_id="trace-1",
        user_id="u1",
        conversation_id="c1",
        agent_name="GeneralAgent",
    )

    assert pending.snapshot.evidence_receipts == ledger.to_snapshot()


@pytest.mark.asyncio
async def test_confirmation_snapshot_resolves_nested_coroutine():
    async def leaked_value():
        return "not awaited"

    leaked = leaked_value()

    class CoroutineState:
        def model_dump(self, mode):
            if mode == "python":
                return {"context": [{"metadata": {"pending": leaked}}]}
            raise ValueError("Unable to serialize unknown type: <class 'coroutine'>")

    registry = PendingAgentScopeConfirmationRegistry()
    agent = SimpleNamespace(state=CoroutineState())

    pending = await registry.register(
        kind="permission",
        agent=agent,
        runner=SimpleNamespace(),
        tools=[],
        native_model=SimpleNamespace(),
        tool_call={"id": "call-coroutine", "name": "Write", "input": {}},
        reply_id="reply-1",
        trace_id="trace-1",
        agent_name="GeneralAgent",
    )

    assert pending.snapshot.agent_state == {
        "context": [{"metadata": {"pending": "not awaited"}}],
    }


@pytest.mark.asyncio
async def test_pending_store_expired_snapshot_returns_none():
    store = PendingAgentScopeStore()
    store.clear()
    snapshot = PendingAgentScopeSnapshot(
        request_id="perm_expired",
        kind="external",
        user_id="u1",
        conversation_id="c1",
        trace_id="trace-1",
        reply_id="reply-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "ext_tool", "input": "{}"},
        agent_state={},
        stream_state={},
        runner_context={},
        created_at=time.time() - PENDING_CONFIRMATION_TTL_SECONDS - 1,
    )
    store._memory_fallback["perm_expired"] = snapshot
    assert await store.pop("perm_expired", user_id="u1") is None


@pytest.mark.asyncio
async def test_pending_store_wrong_user_does_not_consume_snapshot():
    store = PendingAgentScopeStore()
    store.clear()
    snapshot = PendingAgentScopeSnapshot(
        request_id="perm_user_guard",
        kind="permission",
        user_id="owner",
        conversation_id="c1",
        trace_id="trace-1",
        reply_id="reply-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "test_tool", "input": "{}"},
        agent_state={},
        stream_state={},
        runner_context={},
    )
    store._memory_fallback["perm_user_guard"] = snapshot

    assert await store.pop("perm_user_guard", user_id="intruder") is None
    popped = await store.pop("perm_user_guard", user_id="owner")
    assert popped is not None
    assert popped.request_id == "perm_user_guard"


def test_runtime_state_envelope_matches():
    envelope = RuntimeStateEnvelope(
        schema_version=SCHEMA_VERSION,
        agent_name="GeneralAgent",
        agent_version="v1",
        tools_fingerprint="abc123",
        model_name="gpt-test",
        updated_at="2026-01-01T00:00:00Z",
        state={"context": []},
    )
    assert envelope.matches(tools_fingerprint="abc123", agent_name="GeneralAgent")
    assert not envelope.matches(tools_fingerprint="other", agent_name="GeneralAgent")


@pytest.mark.asyncio
async def test_agent_state_store_no_redis_is_noop(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)
    store = AgentStateStore()
    await store.save(
        user_id="u1",
        conversation_id="c1",
        agent_name="GeneralAgent",
        agent_version="v1",
        tools_fingerprint="fp1",
        model_name="gpt-test",
        state={"context": []},
    )
    loaded = await store.load("u1", "c1", "GeneralAgent")
    assert loaded is None


@pytest.mark.asyncio
async def test_serialize_jsonable_resolves_nested_coroutine():
    from app.services.ai.runtime.agentscope.serialize import serialize_jsonable

    async def leaked_value():
        return "awaited"

    payload = await serialize_jsonable(
        {"context": [{"metadata": {"pending": leaked_value()}}]},
        path="agent_state",
    )
    assert payload == {"context": [{"metadata": {"pending": "awaited"}}]}


@pytest.mark.asyncio
async def test_agent_state_store_save_resolves_coroutine(monkeypatch):
    saved: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            saved["key"] = key
            saved["value"] = value

    async def _fake_redis():
        return FakeRedis()

    async def leaked_value():
        return "ready"

    class CoroutineState:
        def model_dump(self, mode):
            if mode == "python":
                return {"context": [{"metadata": {"pending": leaked_value()}}]}
            raise ValueError("Unable to serialize unknown type: <class 'coroutine'>")

    monkeypatch.setattr("app.core.redis.get_redis", _fake_redis)
    store = AgentStateStore()
    await store.save(
        user_id="u1",
        conversation_id="c1",
        agent_name="GeneralAgent",
        agent_version="v1",
        tools_fingerprint="fp1",
        model_name="gpt-test",
        state=CoroutineState(),
    )

    assert "value" in saved
    payload = json.loads(saved["value"])
    assert payload["state"] == {"context": [{"metadata": {"pending": "ready"}}]}


@pytest.mark.asyncio
async def test_confirmation_snapshot_resolves_stream_state_coroutine():
    async def leaked_value():
        return "stream-ready"

    registry = PendingAgentScopeConfirmationRegistry()
    agent = SimpleNamespace(
        state=SimpleNamespace(model_dump=lambda mode: {"context": []})
    )

    pending = await registry.register(
        kind="permission",
        agent=agent,
        runner=SimpleNamespace(),
        tools=[],
        native_model=SimpleNamespace(),
        tool_call={"id": "call-stream", "name": "Bash", "input": {}},
        reply_id="reply-1",
        trace_id="trace-1",
        agent_name="GeneralAgent",
        state={"tool_data": {"x": leaked_value()}},
    )

    assert pending.snapshot.stream_state == {"tool_data": {"x": "stream-ready"}}


@pytest.mark.asyncio
async def test_confirmation_snapshot_resolves_shared_tool_call_coroutine_once():
    from agentscope.message import ToolCallBlock

    async def leaked_suggestions():
        return []

    leaked = leaked_suggestions()
    tool_call = ToolCallBlock.model_construct(
        id="call-shared-coroutine",
        name="Bash",
        input="{}",
        suggested_rules=leaked,
    )

    class StateWithSharedToolCall:
        def model_dump(self, mode):
            assert mode == "python"
            return {
                "context": [
                    {"tool_call": {"suggested_rules": leaked}},
                ],
            }

    registry = PendingAgentScopeConfirmationRegistry()
    pending = await registry.register(
        kind="permission",
        agent=SimpleNamespace(state=StateWithSharedToolCall()),
        runner=SimpleNamespace(),
        tools=[],
        native_model=SimpleNamespace(),
        tool_call=tool_call,
        reply_id="reply-shared-coroutine",
        trace_id="trace-shared-coroutine",
        agent_name="GeneralAgent",
    )

    assert pending.snapshot.tool_call["suggested_rules"] == []
    assert pending.snapshot.agent_state["context"][0]["tool_call"]["suggested_rules"] == []
    assert tool_call.suggested_rules == []

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["active", "consumed", "expired", "foreign", "other-conversation", "scheduled", "terminal", "newer-wait"],
)
async def test_conversation_restores_only_the_current_credential_wait(monkeypatch, case):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.v1.endpoints import chat
    from app.services.ai.runtime.agentscope import pending_store
    from app.services.ai.runtime.conversation_stream_journal import conversation_stream_journal
    from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF

    user_id, conversation_id, request_id = "owner-44", "mail-turn", "credential-fixture"
    store = PendingAgentScopeStore()
    monkeypatch.setattr(pending_store, "pending_agentscope_store", store)
    ref = {"tool_id": "test-mail", "revision": 1, "selection_reason": "credential_selection_required"}
    if case == "scheduled":
        ref["scheduled_task_id"] = 27
    snapshot = PendingAgentScopeSnapshot(
        request_id=request_id, kind="permission",
        user_id="other-user" if case == "foreign" else user_id,
        conversation_id="other-conversation" if case == "other-conversation" else conversation_id,
        trace_id="mail-trace", reply_id="mail-reply", agent_name="Fixture",
        tool_call={"id": "mail-call", "name": "mcp_fixture", "input": '{"limit":1}'},
        agent_state={"fixture_hidden_state": "internal-snapshot-data"},
        stream_state={}, runner_context={MCP_CREDENTIAL_REF: ref},
        created_at=time.time() - (PENDING_CONFIRMATION_TTL_SECONDS + 10 if case == "expired" else 0),
    )
    await store.register(snapshot)
    if case == "consumed":
        await store.pop(request_id, user_id=user_id)
    events = [{
        "_seq": 8, "type": "permission_required", "status": "pending",
        "permission_request_id": request_id, "credential_selection_required": True,
        "id": "mail-call", "title": "Choose test mailbox", "details": "Limit 1",
        "tool_call": {"id": "mail-call", "name": "mcp_fixture", "display_name": "Mailbox", "args": {"limit": 1}},
    }, {
        "_seq": 9, "type": "run_status", "status": "success" if case == "terminal" else "awaiting_permission",
    }]
    if case == "newer-wait":
        events.append({
            "_seq": 10, "type": "permission_required", "status": "pending",
            "permission_request_id": "regular-confirmation", "credential_selection_required": False,
        })
    monkeypatch.setattr(
        conversation_stream_journal, "read_after", AsyncMock(return_value={"events": events, "next_seq": 10}),
    )

    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[chat.require_api_key] = lambda: {"user_id": user_id}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/conversation/{conversation_id}/pending-credential")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    if case != "active":
        assert data is None
        return
    assert data["trace_id"] == "mail-trace"
    assert data["permission_request_id"] == request_id
    assert data["credential_selection_required"] is True
    assert data["supports_session_grant"] is False
    assert data["expires_at"] == int(snapshot.created_at + PENDING_CONFIRMATION_TTL_SECONDS)
    assert data["tool_call"]["args"] == {"limit": 1}
    assert "internal-snapshot-data" not in response.text
    assert await store.peek(request_id, user_id=user_id, strict=True) is not None
