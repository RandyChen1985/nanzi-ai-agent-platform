"""JSONPath 结果经真实运行时/SDK 包装后，持久化仍保留选中的原值。"""
import json
from types import SimpleNamespace

import pytest
from agentscope.event import ToolResultEndEvent
from agentscope.message import ToolResultBlock
from agentscope.tool import ToolResponse

from app.services.ai.runtime.agentscope.tools import AgentScopeRuntimeTool, RuntimeToolSpec
from app.services.secrets.mcp_model_result import ProjectedMcpResult
from app.services.secrets.scanner import SecretScanner

pytestmark = pytest.mark.no_infrastructure
MARKER = "mcp_jsonpath_result"
VALUE = "fixture-known-secret-value"
TOOL = "mcp_fixture"


async def _result_block(*, projected=True, streaming=True):
    payload = {"status": "success", "result_visibility": "projected", "data": {"token": VALUE}}
    result = ProjectedMcpResult(payload) if projected else payload

    async def direct():
        return result

    async def stream():
        yield result

    spec = RuntimeToolSpec(
        name=TOOL, description="Fixture", parameters_schema={"type": "object", "properties": {}},
        source_type="mcp", callable=stream if streaming else direct,
    )
    runtime = AgentScopeRuntimeTool(spec)
    chunk_or_stream = await runtime()
    chunks = [chunk async for chunk in chunk_or_stream] if streaming else [chunk_or_stream]
    response = ToolResponse()
    for chunk in chunks:
        response.append_chunk(chunk)
    return ToolResultBlock(id="call-fixture", name=TOOL, output=response.content,
                           state=response.state, metadata=response.metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_projection_provenance_survives_runtime_and_sdk(streaming):
    block = await _result_block(streaming=streaming)
    assert block.metadata[MARKER] == "v1"
    assert VALUE in block.output[0].text
    assert MARKER not in block.output[0].text
    ordinary = await _result_block(projected=False, streaming=streaming)
    assert MARKER not in ordinary.metadata


@pytest.mark.asyncio
async def test_state_storage_preserves_only_marked_tool_output(monkeypatch):
    from app.services.ai.runtime.agentscope import state_store
    from app.services.secrets import guard

    captured = {}

    class Redis:
        async def set(self, key, value, ex=None):
            captured.update(json.loads(value))

    async def get_redis():
        return Redis()

    async def scanner():
        return SecretScanner([("fixture", VALUE)])

    monkeypatch.setattr("app.core.redis.get_redis", get_redis)
    monkeypatch.setattr(guard, "build_storage_scanner", scanner)
    projected = await _result_block()
    ordinary = await _result_block(projected=False)
    state = {"context": [{"role": "assistant", "content": [
        projected.model_dump(mode="json"), ordinary.model_dump(mode="json"),
        {"type": "tool_call", "name": TOOL, "input": {"password": VALUE}},
        {"type": "text", "text": VALUE, "metadata": {MARKER: "v1"}},
    ]}], "password": VALUE}
    await state_store.agent_state_store.save(
        user_id="fixture", conversation_id="fixture", agent_name="Fixture", agent_version=None,
        tools_fingerprint="fixture", model_name=None, state=state,
    )
    saved = captured["state"]
    blocks = saved["context"][0]["content"]
    assert blocks[0]["output"] == projected.model_dump(mode="json")["output"]
    assert VALUE not in str(blocks[1:])
    assert saved["password"] == "******"
    assert state["password"] == VALUE


@pytest.mark.asyncio
async def test_trace_and_timeline_preserve_only_projected_outputs(monkeypatch):
    from app.services.ai import audit
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
        apply_stream_chunk, finalize_process_timeline,
    )

    block = await _result_block()
    event = ToolResultEndEvent(
        reply_id="fixture", tool_call_id=block.id, state=block.state, metadata=block.metadata,
    )
    runner = object.__new__(AssistantAgentRunner)
    runner.config = SimpleNamespace(agent_name="Fixture", model_name="fixture", temperature=0.0)
    runner.step_counter = 1
    observation = runner._build_tool_observation(
        tool_id=block.id, tool_name=TOOL, tool_args={"password": VALUE},
        tool_output=block.output[0].text, duration_tool=1, target_tool=None, tool_index=0,
        tool_result_state="success", tool_result_metadata=event.metadata,
    )
    timeline = []
    apply_stream_chunk(timeline, observation["log"])
    # Ordinary tool output and tool arguments continue to be scanned.
    apply_stream_chunk(timeline, {"type": "log", "id": "other", "title": "工具完成: Bash",
                                  "details": VALUE, "tool_args": VALUE})
    timeline = finalize_process_timeline(timeline)
    captured = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def add_all(self, rows):
            captured["rows"] = rows

        def add(self, history):
            captured["history"] = history

        async def commit(self):
            pass

    async def scanner():
        return SecretScanner([("fixture", VALUE)])

    monkeypatch.setattr(audit, "AsyncSessionLocal", Session)
    monkeypatch.setattr(audit, "build_storage_scanner", scanner)
    ordinary_trace = observation["trace"].model_copy(update={"meta_info": None})
    await audit.AuditManager.save_trace_logs("fixture", [observation["trace"], ordinary_trace])
    assert captured["rows"][0].tool_output == observation["trace"].tool_output
    assert VALUE not in str(captured["rows"][0].tool_input)
    assert VALUE not in str(captured["rows"][1].tool_output)
    assert await audit.AuditManager.save_history(
        trace_id="fixture", agent_id="fixture", query=VALUE, summary=VALUE,
        user_info={"user_id": 1}, process_timeline=timeline,
    )
    saved = captured["history"]
    assert saved.process_timeline[0]["details"] == observation["log"]["details"]
    assert VALUE not in str(saved.process_timeline[0].get("tool_args"))
    assert VALUE not in str(saved.process_timeline[1])
    assert VALUE not in saved.query and VALUE not in saved.summary
