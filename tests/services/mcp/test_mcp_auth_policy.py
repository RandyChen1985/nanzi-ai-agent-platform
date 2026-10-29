import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from app.services.mcp.mcp_auth_policy import build_mcp_headers
from app.utils.encryption import get_api_key_manager
from app.services.mcp.user_context_assertion import verify_user_assertion
from app.core.context import AgentContext, set_agent_context
from app.services.ai.tools.mcp_factory import current_mcp_agent_identity
from app.models.mcp import McpToolCache
from app.services.ai.tools.mcp_factory import McpToolFactory


pytestmark = pytest.mark.no_infrastructure


def _server(**overrides):
    values = {
        "auth_headers": json.dumps({"Authorization": "Bearer fixed-token"}),
        "credential_mode": "static",
        "user_assertion_enabled": False,
        "user_assertion_header": "X-Nanzi-User-Assertion",
        "user_assertion_audience": None,
        "user_assertion_key_id": None,
    }
    values.update(overrides)
    return type("McpServerStub", (), values)()


def _user_info():
    return {
        "user_id": "123",
        "user_name": "zhangsan",
        "real_name": "张三",
        "extra_data": '{"region":"east","password":"drop"}',
    }


def _agent_info():
    return {
        "agent_id": "agent-1",
        "agent_version_id": "version-1",
        "agent_name": "测试助手",
    }


def test_static_mode_keeps_existing_auth_headers():
    headers = build_mcp_headers(
        _server(),
        user_info=_user_info(),
        agent_info=_agent_info(),
        request_id="req-1",
    )

    assert headers == {"Authorization": "Bearer fixed-token"}


def test_signed_mode_adds_signed_user_assertion_without_exposing_private_data():
    private_key = Ed25519PrivateKey.generate()
    server = _server(
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )

    headers = build_mcp_headers(
        server,
        user_info=_user_info(),
        agent_info=_agent_info(),
        request_id="req-1",
        private_key=private_key,
    )

    assert headers["Authorization"] == "Bearer fixed-token"
    assert headers["X-Request-ID"] == "req-1"
    claims = verify_user_assertion(
        headers["X-Nanzi-User-Assertion"],
        public_key=private_key.public_key(),
        audience="mcp:crm",
    )
    assert claims["sub"] == "nanzi:user:123"
    assert claims["custom_attributes"] == {"region": "east"}
    assert claims["agent_id"] == "agent-1"


def test_user_assertion_is_independent_of_authorization_bearer_token():
    private_key = Ed25519PrivateKey.generate()
    server = _server(
        auth_headers="{}",
        credential_mode="static",
        user_assertion_enabled=True,
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )

    headers = build_mcp_headers(
        server,
        user_info=_user_info(),
        agent_info=_agent_info(),
        request_id="req-no-bearer",
        private_key=private_key,
    )

    assert "Authorization" not in headers
    assert headers["X-Request-ID"] == "req-no-bearer"
    claims = verify_user_assertion(
        headers["X-Nanzi-User-Assertion"],
        public_key=private_key.public_key(),
        audience="mcp:crm",
    )
    assert claims["sub"] == "nanzi:user:123"


def test_signed_mode_requires_audience_and_private_key():
    server = _server(
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )

    with pytest.raises(ValueError, match="private key"):
        build_mcp_headers(
            server,
            user_info=_user_info(),
            agent_info=_agent_info(),
            request_id="req-1",
        )


def test_signed_mode_loads_the_private_key_from_this_mcp_configuration():
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    server = _server(
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="crm-key-1",
        user_assertion_issuer="nanzi-crm",
        user_assertion_private_key_encrypted=get_api_key_manager().encrypt_api_key(pem),
    )

    headers = build_mcp_headers(
        server,
        user_info=_user_info(),
        agent_info=_agent_info(),
        request_id="req-1",
    )

    claims = verify_user_assertion(
        headers["X-Nanzi-User-Assertion"],
        public_key=private_key.public_key(),
        issuer="nanzi-crm",
        audience="mcp:crm",
    )
    assert claims["jti"]


def test_signed_mode_can_use_custom_assertion_header():
    private_key = Ed25519PrivateKey.generate()
    server = _server(
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_header="X-Company-User-Assertion",
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )

    headers = build_mcp_headers(
        server,
        user_info=_user_info(),
        agent_info=_agent_info(),
        request_id="req-1",
        private_key=private_key,
    )

    assert "X-Company-User-Assertion" in headers
    assert "X-Nanzi-User-Assertion" not in headers


def test_signed_mode_uses_default_safe_custom_attributes():
    private_key = Ed25519PrivateKey.generate()
    server = _server(
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )
    headers = build_mcp_headers(
        server,
        user_info={
            **_user_info(),
            "extra_data": '{"region":"east","employee_level":"L3","token":"drop"}',
        },
        agent_info=_agent_info(),
        request_id="req-1",
        private_key=private_key,
    )

    claims = verify_user_assertion(
        headers["X-Nanzi-User-Assertion"],
        public_key=private_key.public_key(),
        audience="mcp:crm",
    )
    assert claims["custom_attributes"] == {"region": "east", "employee_level": "L3"}


def test_current_mcp_agent_identity_comes_from_runtime_context():
    set_agent_context(
        AgentContext(
            agent_id="agent-runtime",
            agent_name="运行时助手",
            agent_version="v3",
            user_id=123,
            user_dimensions={
                "user_name": "zhangsan",
                "real_name": "张三",
                "extra_data": '{"region":"east"}',
            },
        )
    )
    try:
        user_info, agent_info = current_mcp_agent_identity()
    finally:
        set_agent_context(None)

    assert user_info["user_id"] == "123"
    assert user_info["user_name"] == "zhangsan"
    assert agent_info == {
        "agent_id": "agent-runtime",
        "agent_version_id": "v3",
        "agent_name": "运行时助手",
    }


@pytest.mark.asyncio
async def test_mcp_tool_forwards_runtime_identity_to_remote_call(monkeypatch):
    set_agent_context(
        AgentContext(
            agent_id="agent-runtime",
            agent_name="运行时助手",
            agent_version="v3",
            user_id=123,
            trace_id="trace-1",
            user_dimensions={"user_name": "zhangsan"},
        )
    )
    try:
        from unittest.mock import AsyncMock

        mocked_call = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(
            "app.services.ai.tools.mcp_factory.McpClientService.call_remote_tool",
            mocked_call,
        )
        tool = McpToolFactory.create_tool(
            McpToolCache(
                id="tool-1",
                server_id="server-1",
                tool_name="crm:query_customer",
                tool_description="query",
                parameter_schema='{"type":"object","properties":{"customer_id":{"type":"string"}}}',
            )
        )
        produced = await tool.ainvoke({"customer_id": "C-1"})
        # MCP 工具返回异步生成器，必须消费完才会真正发起远端调用
        [item async for item in produced]
    finally:
        set_agent_context(None)

    call_kwargs = mocked_call.await_args.kwargs
    assert call_kwargs["server_id"] == "server-1"
    assert call_kwargs["tool_name"] == "query_customer"
    assert call_kwargs["arguments"] == {"customer_id": "C-1"}
    assert call_kwargs["user_info"] == {"user_name": "zhangsan", "user_id": "123"}
    assert call_kwargs["agent_info"] == {
        "agent_id": "agent-runtime",
        "agent_name": "运行时助手",
        "agent_version_id": "v3",
    }
    assert call_kwargs["request_id"] == "trace-1"
    assert call_kwargs["require_user_context"] is True
