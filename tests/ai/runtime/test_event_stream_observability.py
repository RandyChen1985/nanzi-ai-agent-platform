import pytest
from types import SimpleNamespace

from app.services.ai.runtime.agentscope.event_stream import (
    extract_latest_assistant_text,
    is_interrupt_sse_chunk,
    map_standard_agentscope_event,
    new_native_stream_state,
    stream_observability_agentscope_events,
    stream_pending_tool_interrupt,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_observability_maps_model_call_lifecycle():
    state = new_native_stream_state()
    start = SimpleNamespace(
        type="MODEL_CALL_START",
        reply_id="reply-1",
        model_name="gpt-test",
    )
    end = SimpleNamespace(
        type="MODEL_CALL_END",
        reply_id="reply-1",
        input_tokens=120,
        output_tokens=34,
    )

    start_chunks = []
    async for chunk in stream_observability_agentscope_events(start, state=state):
        start_chunks.append(chunk)
    assert start_chunks[0]["type"] == "model_call"
    assert start_chunks[0]["phase"] == "start"
    assert start_chunks[0]["model_name"] == "gpt-test"

    end_chunks = []
    async for chunk in stream_observability_agentscope_events(end, state=state):
        end_chunks.append(chunk)
    assert end_chunks[0]["phase"] == "end"
    assert end_chunks[0]["input_tokens"] == 120
    assert end_chunks[0]["output_tokens"] == 34
    assert end_chunks[0]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_observability_maps_reply_and_thinking_blocks():
    state = new_native_stream_state()
    events = [
        SimpleNamespace(type="REPLY_START", reply_id="r1", session_id="s1", name="AgentA"),
        SimpleNamespace(type="THINKING_BLOCK_START", reply_id="r1", block_id="think-1"),
        SimpleNamespace(type="THINKING_BLOCK_END", reply_id="r1", block_id="think-1"),
        SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1"),
    ]
    chunks = []
    for event in events:
        async for chunk in stream_observability_agentscope_events(event, state=state):
            chunks.append(chunk)

    assert chunks[0] == {
        "type": "agent_reply",
        "phase": "start",
        "reply_id": "r1",
        "session_id": "s1",
        "agent_name": "AgentA",
    }
    assert chunks[1]["type"] == "thinking" and chunks[1]["phase"] == "start"
    assert chunks[2]["type"] == "thinking" and chunks[2]["phase"] == "end"
    assert chunks[3]["phase"] == "end"


@pytest.mark.asyncio
async def test_custom_state_updated_emits_context_update():
    state = new_native_stream_state()
    event = SimpleNamespace(
        type="CUSTOM",
        name="state_updated",
        value={"tasks": ["compress"]},
    )
    chunks = []
    async for chunk in stream_observability_agentscope_events(event, state=state):
        chunks.append(chunk)
    assert chunks[0]["type"] == "context_update"
    assert chunks[0]["name"] == "state_updated"


@pytest.mark.asyncio
async def test_external_execution_registers_pending(monkeypatch):
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )

    pending_agentscope_confirmations.clear()

    class FakeRunner:
        trace_id = "trace-ext"
        conversation_id = "c-ext"

        def _runtime_user_id(self):
            return "u1"

        def _runtime_agent_name(self):
            return "GeneralAgent"

        def _runner_context(self, *, system_content: str, max_steps: int):
            return {"runner_type": "general", "system_content": system_content, "max_steps": max_steps}

    class FakeAgent:
        class State:
            def model_dump(self, mode="json"):
                return {"context": []}

        state = State()

    event = SimpleNamespace(
        reply_id="reply-ext",
        tool_calls=[
            SimpleNamespace(id="call_ext", name="client_tool", input='{"x": 1}'),
        ],
    )
    runner = FakeRunner()
    chunks = []
    async for chunk in stream_pending_tool_interrupt(
        event=event,
        agent=FakeAgent(),
        runner=runner,
        tools=[],
        native_model=object(),
        state={"system_content": "sys", "max_steps": 5},
        kind="external",
        sse_type="external_execution_required",
    ):
        chunks.append(chunk)

    assert chunks[0]["type"] == "external_execution_required"
    assert chunks[0]["external_execution_request_id"]
    assert is_interrupt_sse_chunk(chunks[0])
    pending = pending_agentscope_confirmations.peek(chunks[0]["external_execution_request_id"])
    assert pending is not None
    assert pending.snapshot.kind == "external"


@pytest.mark.asyncio
async def test_map_standard_agentscope_event_interrupts_on_external_execution():
    state = new_native_stream_state()

    class FakeRunner:
        trace_id = "t"
        conversation_id = "c"

        def _runtime_user_id(self):
            return None

        def _runtime_agent_name(self):
            return "GeneralAgent"

        def _runner_context(self, *, system_content: str, max_steps: int):
            return {}

    class FakeAgent:
        class State:
            def model_dump(self, mode="json"):
                return {}

        state = State()

    event = SimpleNamespace(
        type="REQUIRE_EXTERNAL_EXECUTION",
        reply_id="reply-1",
        tool_calls=[SimpleNamespace(id="c1", name="ext", input="{}")],
    )

    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )

    pending_agentscope_confirmations.clear()

    chunks = []
    async for chunk in map_standard_agentscope_event(
        event,
        state=state,
        agent=FakeAgent(),
        runner=FakeRunner(),
        tools=[],
        native_model=object(),
    ):
        chunks.append(chunk)

    assert any(c.get("type") == "external_execution_required" for c in chunks)


def test_extract_latest_assistant_text_reads_last_assistant_msg():
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    agent = type(
        "FakeAgent",
        (),
        {
            "state": AgentState(
                session_id="s1",
                reply_id="r1",
                context=[
                    Msg(name="user", role="user", content=[TextBlock(text="hi")]),
                    Msg(name="assistant", role="assistant", content=[TextBlock(text="first")]),
                    Msg(name="assistant", role="assistant", content=[TextBlock(text="final answer")]),
                ],
            )
        },
    )()

    assert extract_latest_assistant_text(agent) == "final answer"
