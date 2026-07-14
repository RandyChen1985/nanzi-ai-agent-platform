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
