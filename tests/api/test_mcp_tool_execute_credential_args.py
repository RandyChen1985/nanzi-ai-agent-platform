"""The tester uses the same protected MCP transport in signed and ordinary modes."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp
from app.models.mcp import McpToolCache
from app.services.secrets import mcp_profiles
from app.services.secrets.mcp_model_result import restricted_model_result
from app.utils.context import current_user_info

pytestmark = pytest.mark.no_infrastructure


def _db_and_tool(signed):
    tool = SimpleNamespace(
        id="fixture-tool", server_id="fixture-server", tool_name="mail:list_mail", tool_description="",
        credential_params='["password"]', parameter_schema='{"properties":{"password":{"type":"string"}}}',
        is_available=True, is_published=True,
    )
    server = SimpleNamespace(
        id="fixture-server", sse_url="https://mcp.example.test/rpc",
        scope="global", enabled_status=1, user_assertion_enabled=signed, user_assertion_header=None,
        user_assertion_audience="fixture", user_assertion_issuer=None, user_assertion_key_id=None,
    )

    class DB:
        def __init__(self):
            self.rows = iter([tool, server])

        async def execute(self, _stmt):
            item = next(self.rows)
            return SimpleNamespace(scalar_one_or_none=lambda: item)

        async def get(self, model, _id):
            return tool if model is McpToolCache else None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return DB(), server, tool


@pytest.mark.asyncio
@pytest.mark.parametrize("signed", [True, False])
async def test_debug_uses_one_injection_and_protected_transport(signed, monkeypatch):
    injected = []
    remote = []
    db, server, _ = _db_and_tool(signed)
    user = {"user_id": 1, "role": "admin", "email": "tester@example.test"}

    async def inject(_tool, arguments, **_kwargs):
        injected.append(arguments)
        return {**arguments, "password": "synthetic-value"}

    async def call(**kwargs):
        assert kwargs["user_info"] == user
        assert current_user_info.get() == user
        assert kwargs["credential_call"].tool_id == "fixture-tool"
        prepared = await mcp_profiles.prepare_mcp_arguments(
            db, server, kwargs["tool_name"], kwargs["arguments"], kwargs["credential_call"],
            user_info=kwargs["user_info"], trace_id=None, conversation_id=None,
        )
        remote.append((prepared.arguments, prepared.protect_payload))
        return restricted_model_result("no_matching_fields")

    async def timeout():
        return 2.0

    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", inject)
    monkeypatch.setattr(mcp.McpClientService, "call_remote_tool", call)
    monkeypatch.setattr(mcp, "resolve_mcp_tool_timeout", timeout)
    previous_user = {"user_id": 99, "email": "other@example.test"}
    token = current_user_info.set(previous_user)
    try:
        response = await mcp.execute_mcp_tool(
            "fixture-tool", mcp.ToolExecutionRequest(arguments={"secret_choice_password": "c1"}),
            db=db, user=user,
        )
        assert current_user_info.get() == previous_user
    finally:
        current_user_info.reset(token)
    assert len(injected) == len(remote) == 1
    assert remote[0] == ({"password": "synthetic-value"}, True)
    assert "synthetic-value" not in repr(response)
    assert response["mcp_auth"]["user_assertion_sent"] is signed
    assert response["result"] == {"status": "success", "result_visibility": "restricted"}
    assert response["model_result"] == {
        "tool_name": mcp.build_model_tool_name("mail:list_mail"),
        "visibility": "restricted", "reason": "no_matching_fields",
    }


@pytest.mark.asyncio
async def test_missing_credential_is_a_4xx_and_never_calls_upstream(monkeypatch):
    db, server, _ = _db_and_tool(False)
    user = {"user_id": 1, "role": "admin", "email": "tester@example.test"}

    async def deny(_tool, arguments, *, denials, **_kwargs):
        denials["arg:list_mail:password"] = "denied_by_binding"
        return arguments

    async def call(**kwargs):
        assert kwargs["user_info"] == user
        assert current_user_info.get() == user
        await mcp_profiles.prepare_mcp_arguments(
            db, server, kwargs["tool_name"], kwargs["arguments"], kwargs["credential_call"],
            user_info=kwargs["user_info"], trace_id=None, conversation_id=None,
        )
        pytest.fail("credential preparation should have prevented the upstream call")

    async def timeout():
        return 2.0

    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", deny)
    monkeypatch.setattr(mcp.McpClientService, "call_remote_tool", call)
    monkeypatch.setattr(mcp, "resolve_mcp_tool_timeout", timeout)
    previous_user = {"user_id": 99, "email": "other@example.test"}
    token = current_user_info.set(previous_user)
    try:
        with pytest.raises(HTTPException) as error:
            await mcp.execute_mcp_tool(
                "fixture-tool", mcp.ToolExecutionRequest(arguments={}),
                db=db, user=user,
            )
        assert current_user_info.get() == previous_user
    finally:
        current_user_info.reset(token)
    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "response_kind", "expected"),
    [
        ("http-json", "text-only", {"messages": [["09:12", "Fixture subject"]]}),
        ("http-sse", "text-only", {"messages": [["09:12", "Fixture subject"]]}),
        ("http-json", "root", {"messages": [["09:12", "Fixture subject"]]}),
        ("sdk", "root", {"messages": [["09:12", "Fixture subject"]]}),
        ("http-json", "legacy-text-only", {"messages": [[None, "Fixture subject"]]}),
        ("http-json", "incomplete-envelope", None),
        ("http-bare", "business-data-envelope", {"data": {"messages": [[None, "Fixture subject"]]}}),
        ("http-json", "sdk-string-wrapper", {"messages": [["09:12", "Fixture subject"]]}),
        ("sdk", "sdk-string-wrapper", {"messages": [["09:12", "Fixture subject"]]}),
        ("sdk", "structured-preferred", {"messages": [[None, "Structured subject"]]}),
        ("http-json", "mismatched-wrapper", None),
        ("http-json", "multiple-block-wrapper", None),
        ("http-json", "business-result-key", {"result": [[None, "Fixture subject"]]}),
        ("http-json", "invalid-wrapper-json", {"result": '{"rating": NaN}'}),
        ("http-json", "credential-echo", {"messages": [[None, "fixture-password"]]}),
        ("sdk", "credential-echo", {"messages": [[None, "fixture-password"]]}),
        ("http-json", "echo-check-unavailable", {"messages": [[None, "Fixture subject"]]}),
        ("sdk", "echo-check-unavailable", {"messages": [[None, "Fixture subject"]]}),
        ("http-json", "health-status", {"status": "ok", "checks": {"token": "configured", "mailbox": "ready"}}),
        ("http-sse", "health-status", {"status": "ok", "checks": {"token": "configured", "mailbox": "ready"}}),
        ("sdk", "health-status", {"status": "ok", "checks": {"token": "configured", "mailbox": "ready"}}),
    ],
)
async def test_protected_mcp_wire_result_reaches_tester_and_chat(
    transport, response_kind, expected, monkeypatch,
):
    import asyncio
    import json
    from unittest.mock import AsyncMock

    import httpx
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata
    from mcp.types import CallToolResult, TextContent

    from app.core.context import AgentContext, agent_context
    from app.services.ai.tools import mcp_client, mcp_factory

    db, server, tool = _db_and_tool(False)

    def fixture_mail() -> str:
        return json.dumps({"messages": [["09:12", "Fixture subject"]]})

    text_block = TextContent(type="text", text=fixture_mail())
    select_rows = response_kind in {"text-only", "sdk-string-wrapper"}
    paths = ["$" if response_kind == "root" else "$.messages[*]" if select_rows else "$.messages[*][1]"]
    if response_kind == "health-status":
        paths = ["$"]
        health = json.dumps({"status": "ok", "checks": {"token": "configured", "mailbox": "ready"}})
        wire_result = CallToolResult(
            content=[TextContent(type="text", text=health)], structured_content={"result": health},
        )
    elif response_kind == "sdk-string-wrapper":
        wire_result = func_metadata(fixture_mail).convert_result(fixture_mail())
    elif response_kind == "structured-preferred":
        wire_result = CallToolResult(
            content=[text_block],
            structured_content={"messages": [["09:12", "Structured subject"]]},
        )
    elif response_kind == "mismatched-wrapper":
        wire_result = CallToolResult(content=[text_block], structured_content={"result": "status"})
    elif response_kind == "multiple-block-wrapper":
        wire_result = CallToolResult(
            content=[
                TextContent(type="text", text=fixture_mail()[:10]),
                TextContent(type="text", text=fixture_mail()[10:]),
            ],
            structured_content={"result": fixture_mail()},
        )
    elif response_kind == "business-data-envelope":
        paths = ["$.data.messages[*][1]"]
        wire_result = {"data": {"messages": [["09:12", "Fixture subject"]]}, "content": "Business text"}
    elif response_kind == "business-result-key":
        paths = ["$.result[*][1]"]
        wire_result = CallToolResult(
            content=[text_block],
            structured_content={"result": [["09:12", "Fixture subject"]]},
        )
    elif response_kind == "invalid-wrapper-json":
        paths = ["$.result"]
        invalid_text = '{"rating": NaN}'
        wire_result = CallToolResult(
            content=[TextContent(type="text", text=invalid_text)],
            structured_content={"result": invalid_text},
        )
    elif response_kind == "credential-echo":
        text = json.dumps({"messages": [["09:12", "fixture-password"]]})
        wire_result = CallToolResult(
            content=[TextContent(type="text", text=text)], structured_content={"result": text},
        )
    else:
        wire_result = CallToolResult(content=[text_block])

    tool.model_result_policy = {
        "revision": 1, "target": mcp_profiles.tool_result_target(tool, server), "fields": paths,
    }
    seen = []

    class Transport:
        is_direct_http = transport != "sdk"
        auth_headers = {}
        server_id = server.id
        sse_url = server.sse_url
        mcp_session_id = None

        def __init__(self):
            self.session = self
            self._active_requests_changed = asyncio.Event()

        async def call_tool(self, name, arguments, **kwargs):
            seen.append((name, arguments))
            return wire_result

        def next_rpc_id(self):
            return 1

        def get_http_client(self):
            return self

        def projection_transport_values(self):
            return None if response_kind == "echo-check-unavailable" else ()

        async def post(self, url, *, json: dict, **kwargs):
            seen.append((json["params"]["name"], json["params"]["arguments"]))
            if transport == "http-bare":
                return httpx.Response(200, json=wire_result)

            result = wire_result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if response_kind == "legacy-text-only":
                result.pop("resultType")
            elif response_kind == "incomplete-envelope":
                result["resultType"] = "input_required"
            payload = {"jsonrpc": "2.0", "id": json["id"], "result": result}
            if transport == "http-sse":
                return httpx.Response(200, text=f"data: {encode_json(payload)}\n\n")
            return httpx.Response(200, json=payload)

        async def close(self):
            pass

    encode_json = json.dumps
    monkeypatch.setattr(mcp_client, "AsyncSessionLocal", type(db))
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", AsyncMock(return_value=Transport()))
    monkeypatch.setattr(mcp_client.McpClientService, "_ensure_direct_http_initialized", AsyncMock())
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *args, **kwargs: {})
    monkeypatch.setattr(mcp_client, "apply_mcp_bindings", AsyncMock(return_value={}))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", AsyncMock())
    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", AsyncMock(return_value={"password": "fixture-password"}))
    monkeypatch.setattr(mcp, "resolve_mcp_tool_timeout", AsyncMock(return_value=1))
    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1))

    tester = await mcp.execute_mcp_tool(
        tool.id, mcp.ToolExecutionRequest(arguments={}), db=db, user={"user_id": 1, "role": "admin"},
    )

    token = agent_context.set(AgentContext(agent_id="fixture", agent_name="Fixture", user_id=1))
    try:
        wrapped = mcp_factory.McpToolFactory.create_tool(tool, verify_profile=True)
        stream = await wrapped.ainvoke({})
        chat = [item async for item in stream]
    finally:
        agent_context.reset(token)

    assert seen == [("list_mail", {"password": "fixture-password"})] * 2
    if expected is None:
        assert tester["model_result"]["reason"] == "no_matching_fields"
        assert tester["result"] == {"status": "success", "result_visibility": "restricted"}
    else:
        assert tester["model_result"]["reason"] is None
        assert tester["result"] == {
            "status": "success", "result_visibility": "projected", "data": expected,
        }
    assert chat == [tester["result"]]
