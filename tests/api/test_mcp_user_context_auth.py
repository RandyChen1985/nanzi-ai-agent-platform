from pathlib import Path

import pytest

from app.api.portal.endpoints.mcp import (
    McpServerResponse,
    McpServerWrite,
    _default_user_assertion_audience,
)


pytestmark = pytest.mark.no_infrastructure


def _payload(**overrides):
    payload = {
        "server_name": "CRM",
        "sse_url": "https://crm.example.com/mcp",
        "auth_headers": '{"Authorization":"Bearer fixed-token"}',
    }
    payload.update(overrides)
    return payload


def test_signed_user_mode_allows_system_generated_identity_settings():
    request = McpServerWrite(
        **_payload(
            credential_mode="fixed_token_signed_user",
            user_assertion_enabled=True,
        )
    )
    assert request.user_assertion_audience is None
    assert request.user_assertion_key_id is None
    assert request.user_assertion_issuer == "nanzi-platform"


def test_user_assertion_audience_is_derived_from_mcp_id():
    assert _default_user_assertion_audience("server-123") == "mcp:server-123"


def test_signed_user_mode_accepts_write_only_token_and_policy():
    request = McpServerWrite(
        **_payload(
            credential_mode="fixed_token_signed_user",
            fixed_token="fixed-token",
            user_assertion_enabled=True,
            user_assertion_audience="mcp:crm",
            user_assertion_key_id="key-1",
            user_assertion_issuer="nanzi-platform",
        )
    )

    assert request.fixed_token == "fixed-token"
    assert request.credential_mode == "fixed_token_signed_user"
    assert "fixed_token" not in McpServerResponse.model_fields
    assert "fixed_token_encrypted" not in McpServerResponse.model_fields
    assert "user_assertion_private_key" not in McpServerWrite.model_fields


def test_user_assertion_can_be_enabled_without_bearer_auth_mode():
    request = McpServerWrite(
        **_payload(
            credential_mode="static",
            user_assertion_enabled=True,
            auth_headers="{}",
        )
    )

    assert request.credential_mode == "static"
    assert request.user_assertion_enabled is True


def test_update_model_accepts_write_only_fixed_token():
    request = McpServerWrite(**_payload(fixed_token="rotated-token"))

    assert request.fixed_token == "rotated-token"
    assert "fixed_token" not in request.model_dump()


def test_private_key_is_write_only():
    assert "user_assertion_private_key" not in McpServerResponse.model_fields
    assert "user_assertion_private_key" not in McpServerWrite.model_fields


def test_mcp_response_includes_auth_header_values_for_editing():
    assert "auth_headers" in McpServerResponse.model_fields


def test_legacy_static_mode_remains_valid_without_user_assertion_config():
    request = McpServerWrite(**_payload())
    assert request.credential_mode == "static"
    assert request.user_assertion_enabled is False


def test_mcp_tool_tester_uses_current_user_and_never_returns_assertion_value():
    source = Path("app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")

    assert 'agent_id": "mcp-tool-tester"' in source
    assert "user_info=test_user_info" in source
    assert 'value_masked": "********"' in source
    assert "X-Nanzi-User-Assertion" in source
