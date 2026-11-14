"""合成凭据的实际注入和出站隔离；不连接数据库或远端。"""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.credential_type import McpCredentialProfile, StructuredCredential
from app.models.mcp import McpToolCache
from app.models.secret import UserSecret
from app.services.ai.tools import mcp_client
from app.services.secrets import mcp_profiles
from app.services.secrets.credential_schema import validate_schema
from app.services.secrets.outbound_visibility import protected_transport

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "echo", "error", "timeout", "cancel"])
async def test_mapped_values_stay_in_outbound_call(monkeypatch, caplog, outcome):
    sentinel = "fixture-password-outbound-only"
    values = {"username1": "fixture-login", "password1": sentinel, "host1": "imap.example.test", "port1": 993}
    schema = {"type": "object", "properties": {
        name: {"type": "integer" if name == "port" else "string"}
        for name in ("username", "password", "host", "port", "folder")
    }, "required": ["username", "password", "host", "port"]}
    type_schema = validate_schema({"type": "object", "properties": {
        "username1": {"type": "string"}, "password1": {"type": "string", "writeOnly": True},
        "host1": {"type": "string"}, "port1": {"type": "integer"},
    }, "required": list(values)})
    profile = McpCredentialProfile(
        tool_id="fixture-tool", profile_id="fixture-profile", revision=1,
        server_id="fixture-server", tool_name="mail:list_mail", type_key="fixture_mail",
        type_version=1, selection_mode="infer",
        mappings={name: name + "1" for name in ("username", "password", "host", "port")},
        model_result_fields=["$.messages[*]"],
    )
    tool = McpToolCache(id=profile.tool_id, server_id=profile.server_id, tool_name=profile.tool_name,
                        is_available=True, is_published=True, parameter_schema=json.dumps(schema))
    parent = UserSecret(id="fixture-secret", owner_scope="user", owner_id="101", secret_key="fixture_ref",
                        secret_type="credential", status="active", allowed_hosts=["mcp.example.test"],
                        allowed_scenarios=["mcp"], credential_format="structured_v1")
    payload = StructuredCredential(secret_id=parent.id, owner_scope="user", owner_id="101",
                                   type_key=profile.type_key, type_version=1, value_revision=1, extra_schema={})
    server = SimpleNamespace(id=profile.server_id, enabled_status=1, sse_url="https://mcp.example.test/rpc",
                             scope="personal", user_id=101)
    profile.schema_digest = mcp_profiles.schema_digest(schema)
    profile.endpoint_digest = mcp_profiles.endpoint_digest(server.sse_url)

    class Session:
        async def get(self, model, identity, **kwargs):
            return {McpCredentialProfile: profile, McpToolCache: tool, StructuredCredential: payload}.get(model)

    class Candidate(tuple):
        @property
        def account(self):
            return self[1]

    @asynccontextmanager
    async def session_scope():
        yield Session()

    monkeypatch.setattr(mcp_client, "AsyncSessionLocal", session_scope)
    monkeypatch.setattr(mcp_profiles, "load_payload_config", AsyncMock(return_value=SimpleNamespace(
        require_enabled=lambda: None, cipher=lambda: None)))
    monkeypatch.setattr(mcp_profiles, "invocation_principal", AsyncMock(return_value=SimpleNamespace(id=101, dept_code=None)))
    monkeypatch.setattr(mcp_profiles, "list_candidates", AsyncMock(return_value=[Candidate((parent, "alice", "digest", 1))]))
    monkeypatch.setattr(mcp_profiles.type_catalog, "get_version", AsyncMock(return_value=SimpleNamespace(schema_json=type_schema)))
    monkeypatch.setattr(mcp_profiles.structured_payload, "decrypt_fields", lambda *args: dict(values))
    usage, audits = AsyncMock(), AsyncMock()
    monkeypatch.setattr(mcp_profiles.SecretResolver, "_log_usage", usage)
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *args, **kwargs: {})
    monkeypatch.setattr(mcp_client, "apply_mcp_bindings", AsyncMock(return_value={}))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", audits)
    manager = SimpleNamespace(is_direct_http=True, auth_headers={}, mcp_session_id=None,
                              projection_transport_values=lambda: (), close=AsyncMock())
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", AsyncMock(return_value=manager))
    observed = []

    async def remote(_manager, name, args, **kwargs):
        assert protected_transport.get()
        observed.append(dict(args))
        assert args == {"folder": "inbox", "username": values["username1"], "password": sentinel,
                        "host": values["host1"], "port": 993}
        if outcome == "error":
            raise RuntimeError(sentinel)
        if outcome == "timeout":
            raise asyncio.TimeoutError(sentinel)
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return {"structured_content": {"messages": [{"subject": sentinel if outcome == "echo" else "Fixture mail"}]}}

    monkeypatch.setattr(mcp_client.McpClientService, "_call_direct_http_tool", remote)
    original = {"folder": "inbox", "credential_account": "alice", "password": "model-forgery"}
    snapshot = json.dumps(original, sort_keys=True)
    call = mcp_profiles.McpCredentialInvocation(tool_id=profile.tool_id, profile_id=profile.profile_id,
                                               profile_revision=1, user_query="查一下 alice 的邮件")
    kwargs = dict(user_info={"user_id": "101"}, credential_call=call, for_model=True,
                  conversation_id="fixture-conversation")
    if outcome == "timeout":
        with pytest.raises(TimeoutError) as exc_info:
            await mcp_client.McpClientService.call_remote_tool(server.id, "list_mail", original, **kwargs)
        assert sentinel not in str(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="^CREDENTIAL_TOOL_FAILED$"):
            await mcp_client.McpClientService.call_remote_tool(server.id, "list_mail", original, **kwargs)
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await mcp_client.McpClientService.call_remote_tool(server.id, "list_mail", original, **kwargs)
    else:
        result = await mcp_client.McpClientService.call_remote_tool(server.id, "list_mail", original, **kwargs)
        assert result == {
            "status": "success", "result_visibility": "projected",
            "data": {"messages": [{"subject": sentinel if outcome == "echo" else "Fixture mail"}]},
        }
    if mcp_client._AUDIT_TASKS:
        await asyncio.gather(*list(mcp_client._AUDIT_TASKS))
    assert len(observed) == 1
    assert json.dumps(original, sort_keys=True) == snapshot
    assert not protected_transport.get()
    manager.close.assert_awaited_once()
    assert audits.await_count == 1
    metadata = audits.await_args.kwargs
    assert metadata["arguments"] == {} and metadata["tool_output"] is None
    assert sentinel not in repr(metadata)
    assert sentinel not in repr(usage.await_args)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("stage", ["session", "sdk", "direct_http"])
async def test_timeout_boundaries_hide_credentials_and_preserve_timeout(monkeypatch, protected, stage):
    from app.services.secrets.redact import register_injected, reset_injected

    sentinel = "fixture-timeout-credential"
    reset_injected()
    if not protected:
        register_injected(sentinel)
    timeout = TimeoutError("connection timeout: " + sentinel)
    manager = SimpleNamespace(is_direct_http=stage == "direct_http", auth_headers={},
                              session=object(), close=AsyncMock())
    get_session = AsyncMock(side_effect=timeout) if stage == "session" else AsyncMock(return_value=manager)
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", get_session)
    monkeypatch.setattr(mcp_client, "_call_tool_with_progress", AsyncMock(side_effect=timeout))
    monkeypatch.setattr(mcp_client.McpClientService, "_call_direct_http_tool", AsyncMock(side_effect=timeout))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", AsyncMock())
    try:
        with pytest.raises(TimeoutError) as exc_info:
            await mcp_client.McpClientService.call_remote_tool("fixture-server", "probe", {}, protect_payload=protected)
        assert sentinel not in str(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__
        assert not protected_transport.get()
    finally:
        if mcp_client._AUDIT_TASKS:
            await asyncio.gather(*list(mcp_client._AUDIT_TASKS))
        reset_injected()
