import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services.ai.runtime.agentscope import tools as runtime_tools
from app.services.ai.tools import generic_api
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF


@pytest.mark.asyncio
async def test_read_only_tools_requiring_credentials_go_through_confirmation(monkeypatch):
    async def skip(*args, **kwargs):
        return None

    from app.services.secrets import mcp_profiles

    monkeypatch.setattr(mcp_profiles, "credential_selection_reason", AsyncMock(return_value="credential_selection_required"))
    monkeypatch.setattr(runtime_tools, "_enforce_tool_forbidden", skip)
    monkeypatch.setattr(runtime_tools, "_enforce_command_blacklist", skip)
    spec = runtime_tools.RuntimeToolSpec(
        name="fixture", description="fixture", parameters_schema={"type": "object"}, source_type="mcp",
        callable=skip, permission_scope="read",
        metadata={MCP_CREDENTIAL_REF: {"tool_id": "test", "selection_mode": "ask"}},
    )
    tool = runtime_tools.AgentScopeRuntimeTool(spec, approval_mode="allow", user_id=101)
    assert not await tool.check_read_only({})
    assert not tool.is_concurrency_safe
    decision = await tool.check_permissions({}, None)
    assert decision.behavior.value == "ask"
    assert decision.bypass_immune


def test_authenticated_tool_metadata_uses_local_projection():
    record = SimpleNamespace(
        id="fixture", server_id="s1", tool_name="mail:synthetic-in-remote-name",
        tool_description="synthetic-remote-description", credential_params=None,
        parameter_schema=json.dumps({
            "type": "object",
            "properties": {
                "synthetic-key-from-remote": {"type": "string", "default": "synthetic-echo"},
                "query": {"type": "string"},
            },
        }),
    )
    tool = McpToolFactory.create_tool(record, verify_profile=True)
    assert "synthetic" not in repr((tool.name, tool.description, tool.mcp_input_schema, tool.args_schema.model_json_schema()))
    assert set(tool.mcp_input_schema["properties"]) == {"arg_1", "query"}


@pytest.mark.asyncio
async def test_opaque_mcp_alias_keeps_command_policy_and_input(monkeypatch):
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from app.services.ai.runtime.command_policy import is_shell_like_tool_name
    from app.services.ai.tools.model_tool_name import build_model_tool_name

    async def unused(**kwargs):
        pytest.fail("command permission check should prevent tool execution")

    async def deny(name, arguments, user_id):
        assert name == "sandbox:bash" and is_shell_like_tool_name(name)
        assert arguments["command"] == "echo fixture" and user_id == 101
        return PermissionDecision(behavior=PermissionBehavior.DENY, message="fixture")

    monkeypatch.setattr(runtime_tools, "_enforce_tool_forbidden", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime_tools, "_enforce_command_blacklist", deny)
    spec = runtime_tools.RuntimeToolSpec(
        name=build_model_tool_name("sandbox:bash"), display_name="sandbox:bash",
        description="fixture", parameters_schema={}, source_type="mcp", callable=unused, permission_scope="read",
    )
    runtime = runtime_tools.AgentScopeRuntimeTool(spec, approval_mode="allow", user_id=101)
    assert not await runtime.check_read_only({"command": "echo fixture"})
    assert (await runtime.check_permissions({"command": "echo fixture"}, None)).behavior is PermissionBehavior.DENY


@pytest.mark.asyncio
async def test_unbound_api_sensitive_parameter_is_hidden_and_blocked(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "synthetic")
    config = SimpleNamespace(
        name="fixture", description="fixture", id="api", method="GET",
        url_template="https://api.example.test/resource", headers={},
        parameter_schema={"properties": {
            "clientSecret": {"type": "string", "default": "synthetic-secret-default"},
            "query": {"type": "string"},
        }},
    )
    tool = generic_api.GenericApiToolFactory.create_tool(config)
    assert set(tool.args_schema.model_fields) == {"query"}

    def unexpected_client(**kwargs):
        pytest.fail("misconfigured sensitive parameters must not reach the transport")

    monkeypatch.setattr(generic_api.httpx, "AsyncClient", unexpected_client)
    assert await generic_api.GenericApiToolFactory._execute_request(config, {"query": "fixture"}) == {
        "status": "error", "code": "CREDENTIAL_MAPPING_REQUIRED",
    }


@pytest.mark.asyncio
async def test_fixed_custom_header_upstream_echo_is_not_model_output(monkeypatch):
    real_client = httpx.AsyncClient

    def upstream(request):
        assert request.headers["X-Auth"] == "synthetic-header-value"
        return httpx.Response(200, json={"encoded": "encoded fixture body"}, request=request)

    async def passthrough(_config, headers, **kwargs):
        return headers

    monkeypatch.setattr(generic_api, "apply_api_tool_bindings", passthrough)
    monkeypatch.setattr(generic_api.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
    config = SimpleNamespace(
        id="api", name="fixture", method="GET", url_template="https://api.example.test/resource",
        headers={"X-Auth": "synthetic-header-value"}, parameter_schema={},
        signing_enabled=False,
    )
    assert await generic_api.GenericApiToolFactory._execute_request(config, {}) == {
        "status": "success", "result_visibility": "restricted",
    }



@pytest.mark.asyncio
async def test_mcp_connection_probe_uses_temporary_session_without_db(monkeypatch, caplog):
    from app.services.ai.tools.mcp_client import McpClientService
    from app.services.secrets.outbound_visibility import protected_transport

    async def unexpected_db_call(*args, **kwargs):
        pytest.fail("connection probe must not look up an unsaved server")

    async def load_tools(*args, **kwargs):
        assert protected_transport.get()
        raise RuntimeError("synthetic-probe-error")

    async def get_session(*args, **kwargs):
        return temporary

    temporary = SimpleNamespace(
        sse_url="https://mcp.example.test/sse", auth_headers={"X-Custom": "synthetic-header"},
        is_direct_http=True,
    )
    monkeypatch.setattr(McpClientService, "_sessions", {"verify_fixture": temporary})
    monkeypatch.setattr(McpClientService, "_load_server", unexpected_db_call)
    monkeypatch.setattr(McpClientService, "get_session", get_session)
    monkeypatch.setattr(McpClientService, "_list_direct_http_tools", load_tools)
    with pytest.raises(RuntimeError, match="MCP 凭据连接或工具发现失败"):
        await McpClientService.list_remote_tools("verify_fixture")
    assert "synthetic-probe-error" not in caplog.text
    assert not protected_transport.get()


@pytest.mark.asyncio
async def test_credential_configuration_audit_keeps_only_request_metadata(monkeypatch):
    from fastapi import FastAPI, Request
    from app.core.middleware import AccessLogMiddleware, AuditService

    log = []

    async def record(**kwargs):
        log.append(kwargs)

    monkeypatch.setattr(AuditService, "log_request_data", record)
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.post("/api/portal/mcp/verify")
    async def preview(request: Request):
        return await request.json()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/portal/mcp/verify?query=synthetic-query", json={"custom": "synthetic-payload"},
        )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert log[0]["endpoint"] == "/api/portal/mcp/verify"
    assert log[0]["request_params"] is None
    assert log[0]["response_body"] is None


@pytest.mark.asyncio
async def test_binding_store_failure_blocks_mcp_default_credentials(monkeypatch, caplog):
    from app.core import orm
    from app.services.ai.tools import mcp_client
    from app.services.secrets import injection
    from app.services.secrets.mcp_profiles import CredentialInvocationError

    async def server_snapshot(_id):
        return SimpleNamespace(id="fixture", enabled_status=1, sse_url="https://mcp.example.test/rpc")

    async def unexpected_connection(*args, **kwargs):
        pytest.fail("unknown binding state must not use default credentials")

    def broken_store():
        raise RuntimeError("synthetic-db-error")

    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "synthetic")
    monkeypatch.setattr(orm, "AsyncSessionLocal", broken_store)
    monkeypatch.setattr(injection, "_bindings_unavailable_until", 0.0)
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *args, **kwargs: {"Authorization": "synthetic-default"})
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", server_snapshot)
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", unexpected_connection)
    with pytest.raises(CredentialInvocationError) as error:
        await mcp_client.McpClientService.call_remote_tool("fixture", "list", {}, user_info={"user_id": "101"}, for_model=True)
    assert error.value.code == "CREDENTIAL_ACCESS_DENIED"
    assert "synthetic-db-error" not in caplog.text


@pytest.mark.asyncio
async def test_payload_keyring_configuration_keeps_host_shell_isolated(monkeypatch):
    from app.services.ai import code_execution_service

    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr('{"synthetic":"fixture"}'))
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "")
    subprocess = AsyncMock(side_effect=AssertionError("host shell must remain isolated"))
    monkeypatch.setattr(code_execution_service.asyncio, "create_subprocess_shell", subprocess)
    result = await code_execution_service.run_shell_command_capture("echo fixture")
    assert result.exit_code == 1 and "隔离沙箱" in result.stderr
    subprocess.assert_not_called()
