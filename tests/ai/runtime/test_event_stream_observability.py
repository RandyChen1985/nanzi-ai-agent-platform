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
async def test_map_standard_agentscope_event_emits_reasoning_content_delta():
    state = new_native_stream_state()
    event = SimpleNamespace(type="THINKING_BLOCK_DELTA", delta="先分析")

    chunks = []
    async for chunk in map_standard_agentscope_event(event, state=state):
        chunks.append(chunk)

    assert chunks == [
        {"type": "thinking", "status": "continuing"},
        {"type": "reasoning_content", "content": "先分析"},
    ]


@pytest.mark.asyncio
async def test_map_standard_agentscope_event_records_tool_result_state():
    state = new_native_stream_state()
    event = SimpleNamespace(
        type="TOOL_RESULT_END",
        tool_call_id="tool-1",
        state="success",
    )

    async for _ in map_standard_agentscope_event(event, state=state):
        pass

    assert state["tool_result_states"]["tool-1"] == "success"


@pytest.mark.asyncio
async def test_tool_call_start_preserves_inline_arguments_for_completion_metadata():
    state = new_native_stream_state()
    event = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="read-1",
        tool_call_name="Read",
        arguments={"file_path": "/workspace/docs/report.md"},
    )

    async for _ in map_standard_agentscope_event(event, state=state):
        pass

    async for _ in map_standard_agentscope_event(
        SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="read-1",
            delta='{"file_path": "/workspace/other.txt"}',
        ),
        state=state,
    ):
        pass

    assert state["tool_args_text"]["read-1"] == '{"file_path": "/workspace/docs/report.md"}'


@pytest.mark.asyncio
async def test_tool_call_start_emits_explicit_tool_category():
    state = new_native_stream_state()
    event = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="read-2",
        tool_call_name="Read",
        arguments={"file_path": "/workspace/docs/report.md"},
    )

    chunks = [
        chunk
        async for chunk in map_standard_agentscope_event(
            event,
            state=state,
            emit_observability=False,
        )
    ]

    assert chunks[-1]["category"] == "tool"


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
            return "test_user_ext"

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


@pytest.mark.asyncio
async def test_bash_env_emission_follows_bound_execution_backend(monkeypatch):
    """bash_env SSE 事件必须反映实际绑定的工具后端，而不是配置猜测。"""
    from unittest.mock import AsyncMock

    import app.services.config_service as cfg
    import app.utils.env as env_mod

    FALLBACK = "host"

    async def _run(policy, execution_backend):
        monkeypatch.setattr(
            cfg.ConfigService, "get",
            AsyncMock(return_value=policy),
        )
        # local 策略回落时才调用；其余策略不会触达探测函数
        monkeypatch.setattr(env_mod, "get_env", lambda: FALLBACK)
        state = new_native_stream_state()
        state["execution_backend"] = execution_backend
        event = SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="t1",
            tool_call_name="Bash",
        )
        chunks = []
        async for chunk in map_standard_agentscope_event(event, state=state):
            chunks.append(chunk)
        return [c for c in chunks if c.get("type") == "bash_env"][0][
            "env"
        ]

    assert await _run("docker", "host") == "host"
    assert await _run("docker", "docker") == "docker"
    assert await _run("e2b", "e2b") == "e2b"
    assert await _run("ssh", "ssh") == "ssh"
    assert await _run("local", FALLBACK) == FALLBACK


def test_chatbi_stream_state_preserves_bound_execution_backend():
    from app.services.ai.runners.chatbi.state_serialization import (
        build_stream_state,
        pending_state_to_data_run_state,
    )

    pending_state = {
        "execution_backend": "docker",
        "data_run_state": {},
    }

    data_state, stream_meta = pending_state_to_data_run_state(pending_state)
    stream_state = build_stream_state(data_state, stream_meta)

    assert stream_state["execution_backend"] == "docker"


@pytest.mark.asyncio
async def test_bash_env_emitted_only_once_per_stream(monkeypatch):
    """同一轮内多条 Bash 调用只上报一次 bash_env 事件（state 去重）。"""
    from unittest.mock import AsyncMock

    import app.services.config_service as cfg

    monkeypatch.setattr(
        cfg.ConfigService, "get", AsyncMock(return_value="docker"),
    )
    state = new_native_stream_state()
    event = SimpleNamespace(
        type="TOOL_CALL_START", tool_call_id="t1", tool_call_name="Bash"
    )

    async def drain():
        out = []
        async for chunk in map_standard_agentscope_event(
            event, state=state, emit_observability=False
        ):
            out.append(chunk)
        return out

    first = [c for c in await drain() if c.get("type") == "bash_env"]
    second = [c for c in await drain() if c.get("type") == "bash_env"]
    assert len(first) == 1
    assert second == []


def test_is_interrupt_sse_chunk_only_pauses_for_pending_or_fatal_errors():
    assert is_interrupt_sse_chunk({"type": "permission_required", "permission_request_id": "p1"})
    assert is_interrupt_sse_chunk({"type": "external_execution_required", "external_execution_request_id": "e1"})
    assert is_interrupt_sse_chunk({"type": "error", "status": "error", "content": "fatal"})

    assert not is_interrupt_sse_chunk(
        {
            "type": "log",
            "id": "tool-1",
            "title": "工具完成: Read (120ms)",
            "details": "Error: File does not exist: /tmp/missing.py",
            "status": "error",
        }
    )
    assert not is_interrupt_sse_chunk({"content": "partial answer", "status": "error"})


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


def test_extract_latest_assistant_text_takes_last_block_for_multiblock_msg():
    """工具环同一条 assistant 消息含「过程旁白 + 最终正文」多个 text block 时，
    只应取最后一个 block（最终正文），否则 reconcile 会把旁白与正文拼接、
    当作差额整段补发，造成用户看到重复输出。"""
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    narration = ["两个数据都拿到了。让我获取更多数据。", "数据齐全了。现在整理保存。"]
    body = "## 业务洞察\n" + ("优秀分析结果 " * 10)

    agent = type(
        "FakeAgent",
        (),
        {
            "state": AgentState(
                session_id="s2",
                reply_id="r2",
                context=[
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=[
                            TextBlock(text=narration[0]),
                            TextBlock(text=narration[1]),
                            TextBlock(text=body),
                        ],
                    )
                ],
            )
        },
    )()

    extracted = extract_latest_assistant_text(agent)
    assert extracted == body
    assert "两个数据都拿到了" not in extracted
    assert "数据齐全了" not in extracted


def test_extract_latest_assistant_text_join_fallback_when_last_block_empty():
    """最后一个 block 为空时回退到拼接，避免整条丢失。"""
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    agent = type(
        "FakeAgent",
        (),
        {
            "state": AgentState(
                session_id="s3",
                reply_id="r3",
                context=[
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=[TextBlock(text="正文一"), TextBlock(text="")],
                    )
                ],
            )
        },
    )()

    assert extract_latest_assistant_text(agent) == "正文一"
