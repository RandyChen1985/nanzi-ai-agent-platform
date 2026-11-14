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


@pytest.mark.asyncio
async def test_restricted_result_reason_survives_stream_and_runtime_serialization(monkeypatch):
    from app.services.ai.tools import mcp_factory
    from app.services.secrets.mcp_model_result import project_model_result

    async def call(**kwargs):
        return project_model_result({"structured_content": {"not_selected": VALUE}}, ["$.missing"])

    monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", call)

    async def stream():
        async for part in mcp_factory.stream_remote_tool_with_progress(
            server_id="fixture", tool_name="find", arguments={}, protect_payload=True,
        ):
            yield part

    runtime = AgentScopeRuntimeTool(RuntimeToolSpec(
        name=TOOL, description="Fixture", parameters_schema={"type": "object", "properties": {}},
        source_type="mcp", callable=stream,
    ))
    response = ToolResponse()
    async for chunk in await runtime():
        response.append_chunk(chunk)
    serialized = json.loads(response.content[0].text)
    assert serialized == {
        "status": "success", "result_visibility": "restricted", "reason": "no_matching_fields",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("http_status", "code"), [
    (401, "MCP_UPSTREAM_AUTH_FAILED"),
    (429, "MCP_UPSTREAM_RATE_LIMITED"),
])
async def test_protected_http_uses_only_fixed_error_code(http_status, code, monkeypatch, caplog):
    from unittest.mock import AsyncMock
    import httpx
    from app.services.ai.tools import mcp_client, mcp_factory
    from app.services.secrets.outbound_visibility import protected_transport

    async def respond(request):
        return httpx.Response(http_status, json={"message": VALUE}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = mcp_client.McpSseSession("fixture", "https://example.test/rpc")
        session.get_http_client = lambda: client

        async def remote(**_kwargs):
            token = protected_transport.set(True)
            try:
                return await mcp_client.McpClientService._direct_http_rpc(session, "tools/call", {"name": "find", "arguments": {}})
            finally:
                protected_transport.reset(token)

        mocked = AsyncMock(side_effect=remote)
        monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", mocked)
        assert [part async for part in mcp_factory.stream_remote_tool_with_progress(
            server_id="fixture", tool_name="find", arguments={}, protect_payload=True,
        )] == [{"status": "error", "code": code}]
        assert mocked.await_count == 1
    assert VALUE not in caplog.text


@pytest.mark.parametrize(("http_status", "code"), [
    (401, "MCP_UPSTREAM_AUTH_FAILED"),
    (429, "MCP_UPSTREAM_RATE_LIMITED"),
])
async def test_protected_streamable_http_keeps_status_through_the_sdk(http_status, code, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    import httpx2

    from app.services.ai.tools import mcp_client

    methods = []

    async def respond(request):
        rpc = json.loads(request.content)
        methods.append(rpc["method"])
        if rpc["method"] == "initialize":
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1.0"},
            }})
        if rpc["method"] == "notifications/initialized":
            return httpx2.Response(202)
        return httpx2.Response(http_status, json={"message": VALUE})

    server = SimpleNamespace(
        id="fixture", sse_url="https://example.test/rpc", enabled_status=1, user_assertion_enabled=False,
    )
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *a, **kw: {})
    monkeypatch.setattr(mcp_client, "apply_mcp_bindings", AsyncMock(return_value={}))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", AsyncMock())

    async def get_session(*args, **kwargs):
        session = mcp_client.McpSseSession(server.id, server.sse_url)
        session._create_sse_http_client = lambda **_: httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        session._tcp_endpoint_reachable = AsyncMock(return_value=True)
        await session.connect()
        assert not session.is_direct_http
        return session

    monkeypatch.setattr(mcp_client.McpClientService, "get_session", get_session)
    with pytest.raises(mcp_client.McpProtectedCallError) as error:
        await mcp_client.McpClientService.call_remote_tool(
            server.id, "find", {}, protect_payload=True, timeout_seconds=2, user_info={"user_id": "9"},
        )
    assert error.value.code == code
    assert methods.count("tools/call") == 1
    assert VALUE not in caplog.text


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
